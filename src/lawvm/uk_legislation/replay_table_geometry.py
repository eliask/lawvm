"""UK replay table geometry helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.replay_text import _compact_normalized_text
from lawvm.uk_legislation.uk_grafter import _clean_num


@dataclass(frozen=True, slots=True)
class UKTableOwnedColumnRange:
    start_col: int
    end_col: int
    cell: UKMutableNode
    physical_index: int


@dataclass(frozen=True, slots=True)
class UKTableColumnInsertPlan:
    row_index: int
    row: UKMutableNode
    row_cells: dict[int, UKMutableNode]
    owned_ranges: list[UKTableOwnedColumnRange]


@dataclass(frozen=True, slots=True)
class UKTableCellMatches:
    cells: list[UKMutableNode]
    row_previews: list[str]


@dataclass(frozen=True, slots=True)
class UKTableColumnPayloadCells:
    cells: list[UKMutableNode]
    reason_code: str
    detail: dict[str, object]


@dataclass(frozen=True, slots=True)
class UKTableRowInsertResolution:
    table: UKMutableNode | None
    insert_index: int | None
    reason_code: str
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UKTableRowReplaceSpanResolution:
    table: UKMutableNode | None
    start_index: int | None
    end_index: int | None
    reason_code: str
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UKTableSelectorTables:
    tables: list[UKMutableNode]
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UKTableCellResolution:
    cell: UKMutableNode | None
    reason_code: str
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class UKTableCellsResolution:
    cells: list[UKMutableNode]
    reason_code: str
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _UKTableAnchorRowMatch:
    insert_index: int
    row_preview: str


@dataclass(frozen=True, slots=True)
class _UKTableAnchorTableMatch:
    table: UKMutableNode
    matches: tuple[_UKTableAnchorRowMatch, ...]


@dataclass(frozen=True, slots=True)
class _UKTableEntryCellMatch:
    cell: UKMutableNode
    row_preview: str


ExpandedTableRows: TypeAlias = list[dict[int, UKMutableNode]]
ExpandedTableRowsWithPhysicalIndex: TypeAlias = list[tuple[int, dict[int, UKMutableNode]]]


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


def expanded_uk_table_rows(table: UKMutableNode) -> ExpandedTableRows:
    return [row_cells for _, row_cells in expanded_uk_table_rows_with_physical_index(table)]


def expanded_uk_table_rows_with_physical_index(
    table: UKMutableNode,
) -> ExpandedTableRowsWithPhysicalIndex:
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
) -> UKTableColumnPayloadCells:
    if _uk_kind_value(new_node.kind).lower() != "table":
        return UKTableColumnPayloadCells(
            cells=[],
            reason_code="payload_not_table",
            detail={"payload_kind": _uk_kind_value(new_node.kind)},
        )
    payload_rows = [
        row for row in new_node.children if _uk_kind_value(row.kind).lower() == "row"
    ]
    if not payload_rows:
        return UKTableColumnPayloadCells(
            cells=[],
            reason_code="payload_has_no_rows",
            detail={"payload_row_count": 0},
        )
    payload_cells: list[UKMutableNode] = []
    for row_index, row in enumerate(payload_rows):
        row_cells = [
            child
            for child in row.children
            if _uk_kind_value(child.kind).lower() in {"cell", "header_cell"}
        ]
        if len(row_cells) != 1:
            return UKTableColumnPayloadCells(
                cells=[],
                reason_code="payload_not_single_column",
                detail={
                    "payload_row_index": row_index,
                    "payload_cell_count": len(row_cells),
                    "payload_row_count": len(payload_rows),
                },
            )
        cloned = UKMutableNode.from_irnode(row_cells[0].to_irnode())
        strip_uk_identity_attrs_recursive(cloned)
        payload_cells.append(cloned)
    return UKTableColumnPayloadCells(
        cells=payload_cells,
        reason_code="",
        detail={"payload_row_count": len(payload_rows)},
    )


def uk_table_column_insert_plans(
    table: UKMutableNode,
) -> list[UKTableColumnInsertPlan]:
    plans: list[UKTableColumnInsertPlan] = []
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
        owned_ranges: list[UKTableOwnedColumnRange] = []
        for physical_index, cell in enumerate(row.children):
            cell_kind = _uk_kind_value(cell.kind).lower()
            if cell_kind not in {"cell", "header_cell"}:
                continue
            while col in row_cells:
                col += 1
            rowspan, colspan = uk_table_cell_span(cell)
            start_col = col
            end_col = col + colspan - 1
            owned_ranges.append(
                UKTableOwnedColumnRange(
                    start_col=start_col,
                    end_col=end_col,
                    cell=cell,
                    physical_index=physical_index,
                )
            )
            for offset in range(colspan):
                current_col = col + offset
                row_cells[current_col] = cell
                if rowspan > 1:
                    next_rowspans[current_col] = (rowspan - 1, cell)
            col += colspan
        if row_cells:
            plans.append(
                UKTableColumnInsertPlan(
                    row_index=row_index,
                    row=row,
                    row_cells=row_cells,
                    owned_ranges=owned_ranges,
                )
            )
        active_rowspans = next_rowspans
    return plans


def uk_table_selector_tables(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableSelectorTables:
    tables = [
        child
        for child in node.children
        if _uk_kind_value(child.kind).lower() == "table"
    ]
    if tables:
        return UKTableSelectorTables(tables=tables, detail={"table_carrier": "target"})
    if bool(selector.get("allow_unique_descendant_table")):
        descendant_result = _unique_descendant_uk_tables(node)
        if descendant_result.tables:
            return descendant_result
    if not bool(selector.get("allow_implicit_subsection_one_table")):
        return UKTableSelectorTables(tables=[], detail={"table_carrier": "target"})
    subsection_ones = [
        child
        for child in node.children
        if _uk_kind_value(child.kind).lower() == "subsection" and _clean_num(child.label or "") == "1"
    ]
    if len(subsection_ones) != 1:
        return UKTableSelectorTables(
            tables=[],
            detail={
                "table_carrier": "implicit_subsection_one",
                "subsection_one_count": len(subsection_ones),
            },
        )
    tables = [
        child
        for child in subsection_ones[0].children
        if _uk_kind_value(child.kind).lower() == "table"
    ]
    return UKTableSelectorTables(
        tables=tables,
        detail={"table_carrier": "implicit_subsection_one", "subsection_one_count": 1},
    )


def _unique_row_cells(row_cells: dict[int, UKMutableNode]) -> list[UKMutableNode]:
    cells: list[UKMutableNode] = []
    seen: set[int] = set()
    for _col, cell in sorted(row_cells.items()):
        cell_id = id(cell)
        if cell_id in seen:
            continue
        seen.add(cell_id)
        cells.append(cell)
    return cells


def _is_entry_group_heading(row_cells: dict[int, UKMutableNode]) -> bool:
    cells = _unique_row_cells(row_cells)
    texts = [str(cell.text or "").strip() for cell in cells]
    if len(cells) == 1 and texts[0]:
        return True
    return bool(texts and texts[0] and all(not text for text in texts[1:]))


def _table_entry_article_tolerant_anchor_variants(text: str) -> tuple[str, ...]:
    raw = " ".join(str(text or "").split())
    norm = _compact_normalized_text(raw)
    if not norm:
        return ()
    variants = [norm]
    if raw.lower().startswith("the "):
        variants.append(_compact_normalized_text(raw[4:]))
    else:
        variants.append(f"the{norm}")
    return tuple(dict.fromkeys(variant for variant in variants if variant))


def _unique_descendant_uk_tables(
    node: UKMutableNode,
) -> UKTableSelectorTables:
    matches: list[tuple[UKMutableNode, tuple[str, ...]]] = []

    def _walk(candidate: UKMutableNode, path: tuple[str, ...]) -> None:
        for child_index, child in enumerate(candidate.children):
            child_kind = _uk_kind_value(child.kind).lower()
            child_label = str(child.label or "")
            child_token = (
                f"{child_kind}:{child_label}"
                if child_label
                else f"{child_kind}[{child_index}]"
            )
            child_path = (*path, child_token)
            if child_kind == "table":
                matches.append((child, child_path))
                continue
            _walk(child, child_path)

    _walk(node, ())
    return UKTableSelectorTables(
        tables=[table for table, _path in matches],
        detail={
            "table_carrier": "unique_descendant_table",
            "descendant_table_paths": tuple("/".join(path) for _table, path in matches[:5]),
        },
    )


def resolve_uk_table_entry_row_insert_index(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableRowInsertResolution:
    def result(
        table: UKMutableNode | None,
        insert_index: int | None,
        reason_code: str,
        detail: dict[str, Any],
    ) -> UKTableRowInsertResolution:
        return UKTableRowInsertResolution(
            table=table,
            insert_index=insert_index,
            reason_code=reason_code,
            detail=detail,
        )

    try:
        column_index = int(selector.get("column_index") or 0)
        entry_index = int(selector.get("entry_index") or 0)
    except (TypeError, ValueError):
        return result(None, None, "invalid_selector", {})
    relating_norm = _compact_normalized_text(str(selector.get("relating_text") or ""))
    row_anchor_norms = tuple(
        anchor_norm
        for anchor in selector.get("row_anchor_texts") or ()
        for anchor_norm in (_compact_normalized_text(str(anchor or "")),)
        if anchor_norm
    )
    anchor_entry_label = _clean_num(str(selector.get("anchor_entry_label") or ""))
    direction = str(selector.get("direction") or "")
    selector_mode = str(selector.get("selector_mode") or "ordinal_column")
    if direction not in {"after", "before"}:
        return result(None, None, "invalid_selector", {})
    if selector_mode in {"column_final_entry", "each_column_final_entry"} and direction != "after":
        return result(None, None, "invalid_selector", {})
    if selector_mode == "entry_label":
        if not anchor_entry_label:
            return result(None, None, "invalid_selector", {})
    elif selector_mode == "entry_group_heading":
        if not relating_norm:
            return result(None, None, "invalid_selector", {})
    elif selector_mode == "column_entry":
        if column_index < 1 or not relating_norm:
            return result(None, None, "invalid_selector", {})
    elif selector_mode == "each_column_entry":
        if not relating_norm:
            return result(None, None, "invalid_selector", {})
    elif selector_mode == "column_final_entry":
        if column_index < 1:
            return result(None, None, "invalid_selector", {})
    elif selector_mode == "each_column_final_entry":
        pass
    elif entry_index < 1 or not relating_norm:
        return result(None, None, "invalid_selector", {})
    if selector_mode == "ordinal_column" and column_index < 2:
        return result(None, None, "invalid_selector", {})
    if selector_mode == "relating_entry" and column_index != 1:
        return result(None, None, "invalid_selector", {})
    if selector_mode not in {
        "ordinal_column",
        "relating_entry",
        "entry_label",
        "entry_group_heading",
        "column_entry",
        "each_column_entry",
        "column_final_entry",
        "each_column_final_entry",
    }:
        return result(None, None, "invalid_selector", {})

    table_selection = uk_table_selector_tables(node, selector)
    tables = table_selection.tables
    carrier_detail = table_selection.detail
    if len(tables) != 1:
        if selector_mode == "column_entry":
            anchor_table_matches: list[_UKTableAnchorTableMatch] = []
            for candidate_table in tables:
                candidate_rows = expanded_uk_table_rows_with_physical_index(candidate_table)
                candidate_matches: list[_UKTableAnchorRowMatch] = []
                last_candidate_cell: UKMutableNode | None = None
                for row_index, row_cells in candidate_rows:
                    target_cell = row_cells.get(column_index)
                    if target_cell is None:
                        continue
                    if _compact_normalized_text(target_cell.text or "").find(relating_norm) < 0:
                        continue
                    if target_cell is last_candidate_cell:
                        continue
                    last_candidate_cell = target_cell
                    candidate_matches.append(
                        _UKTableAnchorRowMatch(
                            insert_index=row_index if direction == "before" else row_index + 1,
                            row_preview=" | ".join(
                                str(row_cells[col].text or "")
                                for col in sorted(row_cells)
                                if str(row_cells[col].text or "")
                            )[:240],
                        )
                    )
                if candidate_matches:
                    anchor_table_matches.append(_UKTableAnchorTableMatch(candidate_table, tuple(candidate_matches)))
            if len(anchor_table_matches) == 1 and len(anchor_table_matches[0].matches) == 1:
                table = anchor_table_matches[0].table
                carrier_detail = {
                    **carrier_detail,
                    "table_carrier": "anchor_filtered_descendant_table",
                    "candidate_table_count": len(tables),
                    "anchor_filtered_table_count": 1,
                }
            else:
                return result(
                    None,
                    None,
                    "table_not_unique",
                    {
                        "table_count": len(tables),
                        "anchor_filtered_table_count": len(anchor_table_matches),
                        "anchor_filtered_matches": tuple(
                            {
                                "matching_entry_count": len(table_match.matches),
                                "matching_rows": tuple(match.row_preview for match in table_match.matches[:5]),
                            }
                            for table_match in anchor_table_matches[:5]
                        ),
                        **carrier_detail,
                    },
                )
        else:
            return result(
                None,
                None,
                "table_not_unique",
                {"table_count": len(tables), **carrier_detail},
            )
    else:
        table = tables[0]
    expanded_rows = expanded_uk_table_rows_with_physical_index(table)
    if selector_mode == "each_column_final_entry":
        column_count = max((max(row_cells) for _row_index, row_cells in expanded_rows if row_cells), default=0)
        if column_count < 1:
            return result(
                None,
                None,
                "entry_not_found",
                {
                    "matching_entry_count": 0,
                    "table_column_count": column_count,
                    **carrier_detail,
                },
            )
        return result(
            table,
            len(table.children),
            "",
            {
                "matching_entry_count": len(expanded_rows),
                "table_column_count": column_count,
                "matching_rows": tuple(
                    " | ".join(
                        str(row_cells[col].text or "")
                        for col in sorted(row_cells)
                        if str(row_cells[col].text or "")
                    )[:240]
                    for _row_index, row_cells in expanded_rows[-5:]
                ),
                **carrier_detail,
            },
        )
    if selector_mode == "each_column_entry":
        column_count = max((max(row_cells) for _row_index, row_cells in expanded_rows if row_cells), default=0)
        if column_count < 1:
            return result(
                None,
                None,
                "entry_not_found",
                {
                    "matching_entry_count": 0,
                    "table_column_count": column_count,
                    **carrier_detail,
                },
            )
        matching_each_column_rows: list[tuple[int, str]] = []
        for row_index, row_cells in expanded_rows:
            if not row_cells:
                continue
            if not all(
                column in row_cells
                and _compact_normalized_text(row_cells[column].text or "").find(relating_norm) >= 0
                for column in range(1, column_count + 1)
            ):
                continue
            matching_each_column_rows.append(
                (
                    row_index if direction == "before" else row_index + 1,
                    " | ".join(
                        str(row_cells[col].text or "")
                        for col in sorted(row_cells)
                        if str(row_cells[col].text or "")
                    )[:240],
                )
            )
        if len(matching_each_column_rows) != 1:
            return result(
                None,
                None,
                "entry_not_unique",
                {
                    "matching_entry_count": len(matching_each_column_rows),
                    "table_column_count": column_count,
                    "matching_rows": tuple(row for _index, row in matching_each_column_rows[:5]),
                    **carrier_detail,
                },
            )
        insert_index, row_preview = matching_each_column_rows[0]
        return result(
            table,
            insert_index,
            "",
            {
                "matching_entry_count": 1,
                "matched_row": row_preview,
                "table_column_count": column_count,
                **carrier_detail,
            },
        )
    if selector_mode == "entry_group_heading":
        matching_groups: list[tuple[int, str]] = []
        for row_position, (row_index, row_cells) in enumerate(expanded_rows):
            if not _is_entry_group_heading(row_cells):
                continue
            row_preview = " | ".join(
                str(cell.text or "")
                for cell in _unique_row_cells(row_cells)
                if str(cell.text or "")
            )[:240]
            if _compact_normalized_text(row_preview).find(relating_norm) < 0:
                continue
            insert_index = len(table.children)
            for next_row_index, next_row_cells in expanded_rows[row_position + 1 :]:
                if _is_entry_group_heading(next_row_cells):
                    insert_index = next_row_index
                    break
            matching_groups.append((insert_index, row_preview))
        if len(matching_groups) != 1:
            return result(
                None,
                None,
                "entry_group_heading_not_unique",
                {
                    "matching_entry_count": len(matching_groups),
                    "matching_rows": tuple(row[1] for row in matching_groups[:5]),
                    **carrier_detail,
                },
            )
        insert_index, row_preview = matching_groups[0]
        return result(
            table,
            insert_index,
            "",
            {
                "matching_entry_count": 1,
                "matched_row": row_preview,
                **carrier_detail,
            },
        )

    matching_rows: list[tuple[int, str]] = []
    last_target_cell: UKMutableNode | None = None
    for row_index, row_cells in expanded_rows:
        if selector_mode == "relating_entry":
            row_match_cells = [
                cell
                for _col, cell in sorted(row_cells.items())
                if _compact_normalized_text(cell.text or "").find(relating_norm) >= 0
                or any(
                    _compact_normalized_text(cell.text or "").find(anchor_norm) >= 0
                    for anchor_norm in row_anchor_norms
                )
            ]
            if not row_match_cells:
                continue
            target_cell = row_match_cells[0]
        elif selector_mode == "entry_label":
            target_cell = row_cells.get(1)
            if target_cell is None:
                continue
            if _clean_num(target_cell.text or "") != anchor_entry_label:
                continue
        elif selector_mode == "column_entry":
            target_cell = row_cells.get(column_index)
            if target_cell is None:
                continue
            if _compact_normalized_text(target_cell.text or "").find(relating_norm) < 0:
                continue
        elif selector_mode == "column_final_entry":
            target_cell = row_cells.get(column_index)
            if target_cell is None:
                continue
        else:
            target_cell = row_cells.get(column_index)
            if target_cell is None:
                continue
            relation_cells = [
                cell
                for col, cell in sorted(row_cells.items())
                if col < column_index and _compact_normalized_text(cell.text or "").find(relating_norm) >= 0
            ]
            if not relation_cells:
                continue
        if target_cell is last_target_cell:
            continue
        last_target_cell = target_cell
        insert_index = row_index if direction == "before" else row_index + 1
        if (
            selector_mode == "relating_entry"
            and str(selector.get("source_payload_mode") or "") == "logical_table_entry_group"
        ):
            rowspan, _colspan = uk_table_cell_span(target_cell)
            insert_index = min(len(table.children), row_index + max(rowspan, 1))
        matching_rows.append(
            (
                insert_index,
                " | ".join(
                    str(row_cells[col].text or "")
                    for col in sorted(row_cells)
                    if str(row_cells[col].text or "")
                )[:240],
            )
        )
    if selector_mode == "column_final_entry":
        row_index, _preview = matching_rows[-1] if matching_rows else (None, "")
        if row_index is None:
            return result(
                None,
                None,
                "entry_not_found",
                {
                    "matching_entry_count": 0,
                    **carrier_detail,
                },
            )
        insert_index = min(row_index, len(table.children))
        return result(
            table,
            insert_index,
            "",
            {
                "matching_entry_count": len(matching_rows),
                "matching_rows": tuple(row[1] for row in matching_rows[-5:]),
                **carrier_detail,
            },
        )
    required_entry_index = 1 if selector_mode in {"entry_label", "column_entry"} else entry_index
    if len(matching_rows) < required_entry_index:
        return result(
            None,
            None,
            "entry_not_found",
            {
                "matching_entry_count": len(matching_rows),
                "matching_rows": tuple(row[1] for row in matching_rows[:5]),
                **carrier_detail,
            },
        )
    if selector_mode in {"entry_label", "column_entry"} and len(matching_rows) > 1:
        return result(
            None,
            None,
            "entry_not_unique",
            {
                "matching_entry_count": len(matching_rows),
                "matching_rows": tuple(row[1] for row in matching_rows[:5]),
                **carrier_detail,
            },
        )
    insert_index, row_preview = matching_rows[required_entry_index - 1]
    return result(
        table,
        insert_index,
        "",
        {
            "matching_entry_count": len(matching_rows),
            "matched_row": row_preview,
            **carrier_detail,
        },
    )


def resolve_uk_table_entry_row_replace_span(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableRowReplaceSpanResolution:
    """Resolve rows named by a table-entry replacement selector.

    Replacement is intentionally stricter than insertion: every named relating
    entry must resolve to exactly one physical row, and the resolved rows must
    form a contiguous span.
    """
    def result(
        table: UKMutableNode | None,
        start_index: int | None,
        end_index: int | None,
        reason_code: str,
        detail: dict[str, Any],
    ) -> UKTableRowReplaceSpanResolution:
        return UKTableRowReplaceSpanResolution(
            table=table,
            start_index=start_index,
            end_index=end_index,
            reason_code=reason_code,
            detail=detail,
        )

    selector_mode = str(selector.get("selector_mode") or "")
    if selector_mode != "relating_entries":
        return result(None, None, None, "invalid_selector", {})
    relating_anchor_variants: list[tuple[str, tuple[str, ...]]] = []
    for text in selector.get("relating_texts") or ():
        primary_norm = _compact_normalized_text(str(text or ""))
        variants = _table_entry_article_tolerant_anchor_variants(str(text or ""))
        if primary_norm and variants:
            relating_anchor_variants.append((primary_norm, variants))
    if len(relating_anchor_variants) < 2:
        return result(None, None, None, "invalid_selector", {})

    table_selection = uk_table_selector_tables(node, selector)
    tables = table_selection.tables
    carrier_detail = table_selection.detail
    if len(tables) != 1:
        return result(
            None,
            None,
            None,
            "table_not_unique",
            {"table_count": len(tables), **carrier_detail},
        )

    table = tables[0]
    expanded_rows = expanded_uk_table_rows_with_physical_index(table)
    matches_by_anchor: list[tuple[str, list[tuple[int, str]]]] = []
    for relating_norm, relating_variants in relating_anchor_variants:
        anchor_matches: list[tuple[int, str]] = []
        for row_index, row_cells in expanded_rows:
            row_preview = " | ".join(
                str(row_cells[col].text or "")
                for col in sorted(row_cells)
                if str(row_cells[col].text or "")
            )[:240]
            if not row_preview:
                continue
            if any(
                any(
                    variant and _compact_normalized_text(cell.text or "").find(variant) >= 0
                    for variant in relating_variants
                )
                for cell in _unique_row_cells(row_cells)
            ):
                anchor_matches.append((row_index, row_preview))
        matches_by_anchor.append((relating_norm, anchor_matches))

    non_unique = [
        {
            "anchor": anchor,
            "matching_entry_count": len(matches),
            "matching_rows": tuple(row for _index, row in matches[:5]),
        }
        for anchor, matches in matches_by_anchor
        if len(matches) != 1
    ]
    if non_unique:
        reason = (
            "entry_not_found"
            if any(int(item["matching_entry_count"]) == 0 for item in non_unique)
            else "entry_not_unique"
        )
        return result(
            None,
            None,
            None,
            reason,
            {
                "anchor_matches": tuple(non_unique),
                **carrier_detail,
            },
        )

    matched_rows = tuple(matches[0] for _anchor, matches in matches_by_anchor)
    row_indices = sorted({index for index, _preview in matched_rows})
    if len(row_indices) != len(matched_rows):
        return result(
            None,
            None,
            None,
            "entries_share_row",
            {
                "matched_rows": tuple(preview for _index, preview in matched_rows),
                **carrier_detail,
            },
        )
    start = row_indices[0]
    end_exclusive = row_indices[-1] + 1
    if row_indices != list(range(start, end_exclusive)):
        return result(
            None,
            None,
            None,
            "entry_span_not_contiguous",
            {
                "row_indices": tuple(row_indices),
                "matched_rows": tuple(preview for _index, preview in matched_rows),
                **carrier_detail,
            },
        )
    return result(
        table,
        start,
        end_exclusive,
        "",
        {
            "matching_entry_count": len(matched_rows),
            "matched_rows": tuple(preview for _index, preview in matched_rows),
            "replace_start_index": start,
            "replace_end_index": end_exclusive,
            **carrier_detail,
        },
    )


def resolve_unique_uk_table_relating_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableCellResolution:
    def result(
        cell: UKMutableNode | None,
        reason_code: str,
        detail: dict[str, Any],
    ) -> UKTableCellResolution:
        return UKTableCellResolution(cell=cell, reason_code=reason_code, detail=detail)

    try:
        column_index = int(selector.get("column_index") or 0)
    except (TypeError, ValueError):
        return result(None, "invalid_selector", {})
    relating_norm = _compact_normalized_text(str(selector.get("relating_text") or ""))
    if column_index < 1 or not relating_norm:
        return result(None, "invalid_selector", {})

    table_selection = uk_table_selector_tables(node, selector)
    tables = table_selection.tables
    carrier_detail = table_selection.detail
    candidate_tables = tables
    if len(candidate_tables) != 1:
        filtered_tables: list[UKMutableNode] = []
        filtered_rows: list[str] = []
        for candidate_table in candidate_tables:
            candidate_matches = _matching_uk_table_relating_cells(
                candidate_table,
                column_index=column_index,
                relating_norm=relating_norm,
            )
            if len(candidate_matches.cells) == 1:
                filtered_tables.append(candidate_table)
                filtered_rows.extend(candidate_matches.row_previews[:1])
        if len(filtered_tables) != 1:
            return result(
                None,
                "table_not_unique",
                {
                    "table_count": len(candidate_tables),
                    "anchor_filtered_table_count": len(filtered_tables),
                    "anchor_filtered_rows": tuple(filtered_rows[:5]),
                    **carrier_detail,
                },
            )
        candidate_tables = filtered_tables
        carrier_detail = {
            **carrier_detail,
            "table_carrier": "anchor_filtered_descendant_table",
            "candidate_table_count": len(tables),
            "anchor_filtered_table_count": 1,
        }

    matches = _matching_uk_table_relating_cells(
        candidate_tables[0],
        column_index=column_index,
        relating_norm=relating_norm,
    )
    if len(matches.cells) == 1:
        return result(
            matches.cells[0],
            "",
            {
                "matching_cell_count": 1,
                "matched_row": matches.row_previews[0] if matches.row_previews else "",
                **carrier_detail,
            },
        )
    reason = "relating_cell_not_found" if not matches.cells else "relating_cell_ambiguous"
    return result(
        None,
        reason,
        {
            "matching_cell_count": len(matches.cells),
            "matching_rows": tuple(matches.row_previews[:5]),
            **carrier_detail,
        },
    )


def _matching_uk_table_relating_cells(
    table: UKMutableNode,
    *,
    column_index: int,
    relating_norm: str,
) -> UKTableCellMatches:
    matching_cells: list[UKMutableNode] = []
    matching_rows: list[str] = []
    for row_cells in expanded_uk_table_rows(table):
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
    return UKTableCellMatches(cells=matching_cells, row_previews=matching_rows)


def resolve_unique_uk_table_entry_cells(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableCellsResolution:
    def result(
        cells: list[UKMutableNode],
        reason_code: str,
        detail: dict[str, Any],
    ) -> UKTableCellsResolution:
        return UKTableCellsResolution(cells=cells, reason_code=reason_code, detail=detail)

    try:
        column_index = int(selector.get("column_index") or 0)
    except (TypeError, ValueError):
        return result([], "invalid_selector", {})
    raw_labels = selector.get("entry_labels")
    if not isinstance(raw_labels, (list, tuple)) or not raw_labels:
        return result([], "invalid_selector", {})
    entry_labels = tuple(_compact_normalized_text(str(label or "")) for label in raw_labels)
    if column_index < 1 or not entry_labels or any(not label for label in entry_labels):
        return result([], "invalid_selector", {})

    table_selection = uk_table_selector_tables(node, selector)
    tables = table_selection.tables
    carrier_detail = table_selection.detail
    if len(tables) != 1:
        return result([], "table_not_unique", {"table_count": len(tables), **carrier_detail})

    matches_by_label: dict[str, list[_UKTableEntryCellMatch]] = {label: [] for label in entry_labels}
    for row_cells in expanded_uk_table_rows(tables[0]):
        row_texts = [
            str(row_cells[col].text or "")
            for col in sorted(row_cells)
            if str(row_cells[col].text or "")
        ]
        target_cell = row_cells.get(column_index)
        if target_cell is None:
            continue
        for label in entry_labels:
            if not any(_compact_normalized_text(text) == label for text in row_texts):
                continue
            row_preview = " | ".join(row_texts)[:240]
            if not matches_by_label[label] or matches_by_label[label][-1].cell is not target_cell:
                matches_by_label[label].append(_UKTableEntryCellMatch(target_cell, row_preview))

    missing = tuple(label for label, matches in matches_by_label.items() if not matches)
    ambiguous = tuple(label for label, matches in matches_by_label.items() if len(matches) > 1)
    if missing or ambiguous:
        return result(
            [],
            "entry_cells_not_unique",
            {
                "missing_entry_labels": missing,
                "ambiguous_entry_labels": ambiguous,
                "matching_rows": tuple(
                    match.row_preview
                    for matches in matches_by_label.values()
                    for match in matches[:2]
                )[:5],
                **carrier_detail,
            },
        )
    return result(
        [matches_by_label[label][0].cell for label in entry_labels],
        "",
        {
            "matching_cell_count": len(entry_labels),
            "matched_rows": tuple(matches_by_label[label][0].row_preview for label in entry_labels),
            **carrier_detail,
        },
    )


def resolve_unique_uk_table_entry_text_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableCellResolution:
    def result(
        cell: UKMutableNode | None,
        reason_code: str,
        detail: dict[str, Any],
    ) -> UKTableCellResolution:
        return UKTableCellResolution(cell=cell, reason_code=reason_code, detail=detail)

    match_norm = _compact_normalized_text(str(selector.get("match_text") or ""))
    selector_mode = str(selector.get("selector_mode") or "")
    relating_norm = _compact_normalized_text(str(selector.get("relating_text") or ""))
    entry_label_norm = _compact_normalized_text(str(selector.get("entry_label") or ""))
    if not match_norm or (
        selector_mode == "unique_relating_text" and not relating_norm
    ) or (selector_mode == "unique_entry_text" and not entry_label_norm):
        return result(None, "invalid_selector", {})

    table_selection = uk_table_selector_tables(node, selector)
    tables = table_selection.tables
    carrier_detail = table_selection.detail
    candidate_tables = tables
    if len(candidate_tables) != 1:
        filtered_tables: list[UKMutableNode] = []
        filtered_rows: list[str] = []
        for candidate_table in candidate_tables:
            candidate_matches = _matching_uk_table_entry_text_cells(
                candidate_table,
                selector_mode=selector_mode,
                match_norm=match_norm,
                relating_norm=relating_norm,
                entry_label_norm=entry_label_norm,
            )
            if len(candidate_matches.cells) == 1:
                filtered_tables.append(candidate_table)
                filtered_rows.extend(candidate_matches.row_previews[:1])
        if len(filtered_tables) != 1:
            return result(
                None,
                "table_not_unique",
                {
                    "table_count": len(candidate_tables),
                    "anchor_filtered_table_count": len(filtered_tables),
                    "anchor_filtered_rows": tuple(filtered_rows[:5]),
                    **carrier_detail,
                },
            )
        candidate_tables = filtered_tables
        carrier_detail = {
            **carrier_detail,
            "table_carrier": "anchor_filtered_descendant_table",
            "candidate_table_count": len(tables),
            "anchor_filtered_table_count": 1,
        }

    matches = _matching_uk_table_entry_text_cells(
        candidate_tables[0],
        selector_mode=selector_mode,
        match_norm=match_norm,
        relating_norm=relating_norm,
        entry_label_norm=entry_label_norm,
    )
    if len(matches.cells) == 1:
        return result(
            matches.cells[0],
            "",
            {
                "matching_cell_count": 1,
                "matched_row": matches.row_previews[0] if matches.row_previews else "",
                **carrier_detail,
            },
        )
    reason = "cell_text_not_found" if not matches.cells else "cell_text_ambiguous"
    return result(
        None,
        reason,
        {
            "matching_cell_count": len(matches.cells),
            "matching_rows": tuple(matches.row_previews[:5]),
            **carrier_detail,
        },
    )


def _matching_uk_table_entry_text_cells(
    table: UKMutableNode,
    *,
    selector_mode: str,
    match_norm: str,
    relating_norm: str,
    entry_label_norm: str,
) -> UKTableCellMatches:
    matching_cells: list[UKMutableNode] = []
    matching_rows: list[str] = []
    for row_cells in expanded_uk_table_rows(table):
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
            cell_norm = _compact_normalized_text(cell.text or "")
            if selector_mode == "unique_table_text":
                if cell_norm != match_norm:
                    continue
            elif cell_norm.find(match_norm) < 0:
                continue
            if not matching_cells or matching_cells[-1] is not cell:
                matching_cells.append(cell)
                matching_rows.append(" | ".join(row_texts)[:240])
    return UKTableCellMatches(cells=matching_cells, row_previews=matching_rows)


def resolve_unique_uk_table_entry_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableCellResolution:
    def result(
        cell: UKMutableNode | None,
        reason_code: str,
        detail: dict[str, Any],
    ) -> UKTableCellResolution:
        return UKTableCellResolution(cell=cell, reason_code=reason_code, detail=detail)

    try:
        column_index = int(selector.get("column_index") or 0)
    except (TypeError, ValueError):
        return result(None, "invalid_selector", {})
    entry_label_norm = _compact_normalized_text(str(selector.get("entry_label") or ""))
    if column_index < 1 or not entry_label_norm:
        return result(None, "invalid_selector", {})

    table_selection = uk_table_selector_tables(node, selector)
    tables = table_selection.tables
    carrier_detail = table_selection.detail
    if len(tables) != 1:
        return result(None, "table_not_unique", {"table_count": len(tables), **carrier_detail})

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
        return result(
            matching_cells[0],
            "",
            {
                "matching_cell_count": 1,
                "matched_row": matching_rows[0] if matching_rows else "",
                **carrier_detail,
            },
        )
    reason = "entry_cell_not_found" if not matching_cells else "entry_cell_ambiguous"
    return result(
        None,
        reason,
        {
            "matching_cell_count": len(matching_cells),
            "matching_rows": tuple(matching_rows[:5]),
            **carrier_detail,
        },
    )


def resolve_unique_uk_table_column_text_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableCellResolution:
    def result(
        cell: UKMutableNode | None,
        reason_code: str,
        detail: dict[str, Any],
    ) -> UKTableCellResolution:
        return UKTableCellResolution(cell=cell, reason_code=reason_code, detail=detail)

    try:
        column_index = int(selector.get("column_index") or 0)
        row_index = int(selector.get("row_index") or 0)
    except (TypeError, ValueError):
        return result(None, "invalid_selector", {})
    match_norm = _compact_normalized_text(str(selector.get("match_text") or ""))
    full_cell_match = str(selector.get("match_scope") or "") == "full_cell"
    if column_index < 1 or not match_norm:
        return result(None, "invalid_selector", {})

    table_selection = uk_table_selector_tables(node, selector)
    tables = table_selection.tables
    carrier_detail = table_selection.detail
    candidate_tables = tables
    if len(candidate_tables) != 1:
        filtered_tables: list[UKMutableNode] = []
        filtered_rows: list[str] = []
        for candidate_table in candidate_tables:
            candidate_matches = _matching_uk_table_column_text_cells(
                candidate_table,
                column_index=column_index,
                row_index=row_index,
                match_norm=match_norm,
                full_cell_match=full_cell_match,
            )
            if len(candidate_matches.cells) == 1:
                filtered_tables.append(candidate_table)
                filtered_rows.extend(candidate_matches.row_previews[:1])
        if len(filtered_tables) != 1:
            return result(
                None,
                "table_not_unique",
                {
                    "table_count": len(candidate_tables),
                    "anchor_filtered_table_count": len(filtered_tables),
                    "anchor_filtered_rows": tuple(filtered_rows[:5]),
                    **carrier_detail,
                },
            )
        candidate_tables = filtered_tables
        carrier_detail = {
            **carrier_detail,
            "table_carrier": "anchor_filtered_descendant_table",
            "candidate_table_count": len(tables),
            "anchor_filtered_table_count": 1,
        }

    matches = _matching_uk_table_column_text_cells(
        candidate_tables[0],
        column_index=column_index,
        row_index=row_index,
        match_norm=match_norm,
        full_cell_match=full_cell_match,
    )
    if len(matches.cells) == 1:
        return result(
            matches.cells[0],
            "",
            {
                "matching_cell_count": 1,
                "matched_row": matches.row_previews[0] if matches.row_previews else "",
                **carrier_detail,
            },
        )
    reason = "cell_text_not_found" if not matches.cells else "cell_text_ambiguous"
    return result(
        None,
        reason,
        {
            "matching_cell_count": len(matches.cells),
            "matching_rows": tuple(matches.row_previews[:5]),
            **carrier_detail,
        },
    )


def _matching_uk_table_column_text_cells(
    table: UKMutableNode,
    *,
    column_index: int,
    row_index: int = 0,
    match_norm: str,
    full_cell_match: bool = False,
) -> UKTableCellMatches:
    matching_cells: list[UKMutableNode] = []
    matching_rows: list[str] = []
    for physical_row_index, row_cells in expanded_uk_table_rows_with_physical_index(table):
        if row_index >= 1 and physical_row_index + 1 != row_index:
            continue
        target_cell = row_cells.get(column_index)
        if target_cell is None:
            continue
        cell_norm = _compact_normalized_text(target_cell.text or "")
        if full_cell_match and cell_norm != match_norm:
            continue
        if not full_cell_match and cell_norm.find(match_norm) < 0:
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
    return UKTableCellMatches(cells=matching_cells, row_previews=matching_rows)


def resolve_uk_table_entry_inline_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> UKTableCellResolution:
    """Resolve a source-owned "nth entry in column N relating to X" table cell."""
    def result(
        cell: UKMutableNode | None,
        reason_code: str,
        detail: dict[str, Any],
    ) -> UKTableCellResolution:
        return UKTableCellResolution(cell=cell, reason_code=reason_code, detail=detail)

    selector_mode = str(selector.get("selector_mode") or "")
    if selector_mode == "unique_column_text":
        return resolve_unique_uk_table_column_text_cell(node, selector)
    if selector_mode == "unique_relating_cell":
        return resolve_unique_uk_table_relating_cell(node, selector)
    if selector_mode in {"unique_relating_text", "unique_entry_text"}:
        return resolve_unique_uk_table_entry_text_cell(node, selector)
    if selector_mode == "unique_table_text":
        return resolve_unique_uk_table_entry_text_cell(node, selector)
    if selector_mode == "unique_entry_cell":
        return resolve_unique_uk_table_entry_cell(node, selector)

    try:
        column_index = int(selector.get("column_index") or 0)
        entry_index = int(selector.get("entry_index") or 0)
    except (TypeError, ValueError):
        return result(None, "invalid_selector", {})
    relating_norm = _compact_normalized_text(str(selector.get("relating_text") or ""))
    if column_index < 1 or entry_index < 1 or not relating_norm:
        return result(None, "invalid_selector", {})

    table_selection = uk_table_selector_tables(node, selector)
    tables = table_selection.tables
    carrier_detail = table_selection.detail
    if len(tables) != 1:
        return result(None, "table_not_unique", {"table_count": len(tables), **carrier_detail})

    matching_cells: list[UKMutableNode] = []
    matching_rows: list[str] = []
    for row_cells in expanded_uk_table_rows(tables[0]):
        target_cell = row_cells.get(column_index)
        if target_cell is None:
            continue
        relation_cells = [
            cell
            for col, cell in sorted(row_cells.items())
            if col < column_index and _compact_normalized_text(cell.text or "").find(relating_norm) >= 0
        ]
        if not relation_cells:
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
    if len(matching_cells) < entry_index:
        return result(
            None,
            "entry_not_found",
            {
                "matching_entry_count": len(matching_cells),
                "matching_rows": tuple(matching_rows[:5]),
                **carrier_detail,
            },
        )
    return result(
        matching_cells[entry_index - 1],
        "",
        {
            "matching_entry_count": len(matching_cells),
            "matched_row": matching_rows[entry_index - 1] if entry_index - 1 < len(matching_rows) else "",
            **carrier_detail,
        },
    )

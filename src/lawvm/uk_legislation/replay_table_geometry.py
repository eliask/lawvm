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
    if tables:
        return tables, {"table_carrier": "target"}
    if bool(selector.get("allow_unique_descendant_table")):
        descendant_tables, descendant_detail = _unique_descendant_uk_tables(node)
        if descendant_tables:
            return descendant_tables, descendant_detail
    if not bool(selector.get("allow_implicit_subsection_one_table")):
        return [], {"table_carrier": "target"}
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
) -> tuple[list[UKMutableNode], dict[str, Any]]:
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
    return [table for table, _path in matches], {
        "table_carrier": "unique_descendant_table",
        "descendant_table_paths": tuple("/".join(path) for _table, path in matches[:5]),
    }


def resolve_uk_table_entry_row_insert_index(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[UKMutableNode | None, int | None, str, dict[str, Any]]:
    try:
        column_index = int(selector.get("column_index") or 0)
        entry_index = int(selector.get("entry_index") or 0)
    except (TypeError, ValueError):
        return None, None, "invalid_selector", {}
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
        return None, None, "invalid_selector", {}
    if selector_mode == "column_final_entry" and direction != "after":
        return None, None, "invalid_selector", {}
    if selector_mode == "entry_label":
        if not anchor_entry_label:
            return None, None, "invalid_selector", {}
    elif selector_mode == "entry_group_heading":
        if not relating_norm:
            return None, None, "invalid_selector", {}
    elif selector_mode == "column_entry":
        if column_index < 1 or not relating_norm:
            return None, None, "invalid_selector", {}
    elif selector_mode == "column_final_entry":
        if column_index < 1:
            return None, None, "invalid_selector", {}
    elif entry_index < 1 or not relating_norm:
        return None, None, "invalid_selector", {}
    if selector_mode == "ordinal_column" and column_index < 2:
        return None, None, "invalid_selector", {}
    if selector_mode == "relating_entry" and column_index != 1:
        return None, None, "invalid_selector", {}
    if selector_mode not in {
        "ordinal_column",
        "relating_entry",
        "entry_label",
        "entry_group_heading",
        "column_entry",
        "column_final_entry",
    }:
        return None, None, "invalid_selector", {}

    tables, carrier_detail = uk_table_selector_tables(node, selector)
    if len(tables) != 1:
        return None, None, "table_not_unique", {"table_count": len(tables), **carrier_detail}

    table = tables[0]
    expanded_rows = expanded_uk_table_rows_with_physical_index(table)
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
            return None, None, "entry_group_heading_not_unique", {
                "matching_entry_count": len(matching_groups),
                "matching_rows": tuple(row[1] for row in matching_groups[:5]),
                **carrier_detail,
            }
        insert_index, row_preview = matching_groups[0]
        return table, insert_index, "", {
            "matching_entry_count": 1,
            "matched_row": row_preview,
            **carrier_detail,
        }

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
            return None, None, "entry_not_found", {
                "matching_entry_count": 0,
                **carrier_detail,
            }
        insert_index = min(row_index, len(table.children))
        return table, insert_index, "", {
            "matching_entry_count": len(matching_rows),
            "matching_rows": tuple(row[1] for row in matching_rows[-5:]),
            **carrier_detail,
        }
    required_entry_index = 1 if selector_mode in {"entry_label", "column_entry"} else entry_index
    if len(matching_rows) < required_entry_index:
        return None, None, "entry_not_found", {
            "matching_entry_count": len(matching_rows),
            "matching_rows": tuple(row[1] for row in matching_rows[:5]),
            **carrier_detail,
        }
    if selector_mode in {"entry_label", "column_entry"} and len(matching_rows) > 1:
        return None, None, "entry_not_unique", {
            "matching_entry_count": len(matching_rows),
            "matching_rows": tuple(row[1] for row in matching_rows[:5]),
            **carrier_detail,
        }
    insert_index, row_preview = matching_rows[required_entry_index - 1]
    return table, insert_index, "", {
        "matching_entry_count": len(matching_rows),
        "matched_row": row_preview,
        **carrier_detail,
    }


def resolve_uk_table_entry_row_replace_span(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[UKMutableNode | None, int | None, int | None, str, dict[str, Any]]:
    """Resolve rows named by a table-entry replacement selector.

    Replacement is intentionally stricter than insertion: every named relating
    entry must resolve to exactly one physical row, and the resolved rows must
    form a contiguous span.
    """
    selector_mode = str(selector.get("selector_mode") or "")
    if selector_mode != "relating_entries":
        return None, None, None, "invalid_selector", {}
    relating_anchor_variants: list[tuple[str, tuple[str, ...]]] = []
    for text in selector.get("relating_texts") or ():
        primary_norm = _compact_normalized_text(str(text or ""))
        variants = _table_entry_article_tolerant_anchor_variants(str(text or ""))
        if primary_norm and variants:
            relating_anchor_variants.append((primary_norm, variants))
    if len(relating_anchor_variants) < 2:
        return None, None, None, "invalid_selector", {}

    tables, carrier_detail = uk_table_selector_tables(node, selector)
    if len(tables) != 1:
        return None, None, None, "table_not_unique", {"table_count": len(tables), **carrier_detail}

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
        return None, None, None, reason, {
            "anchor_matches": tuple(non_unique),
            **carrier_detail,
        }

    matched_rows = tuple(matches[0] for _anchor, matches in matches_by_anchor)
    row_indices = sorted({index for index, _preview in matched_rows})
    if len(row_indices) != len(matched_rows):
        return None, None, None, "entries_share_row", {
            "matched_rows": tuple(preview for _index, preview in matched_rows),
            **carrier_detail,
        }
    start = row_indices[0]
    end_exclusive = row_indices[-1] + 1
    if row_indices != list(range(start, end_exclusive)):
        return None, None, None, "entry_span_not_contiguous", {
            "row_indices": tuple(row_indices),
            "matched_rows": tuple(preview for _index, preview in matched_rows),
            **carrier_detail,
        }
    return table, start, end_exclusive, "", {
        "matching_entry_count": len(matched_rows),
        "matched_rows": tuple(preview for _index, preview in matched_rows),
        "replace_start_index": start,
        "replace_end_index": end_exclusive,
        **carrier_detail,
    }


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


def resolve_unique_uk_table_entry_cells(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[list[UKMutableNode], str, dict[str, Any]]:
    try:
        column_index = int(selector.get("column_index") or 0)
    except (TypeError, ValueError):
        return [], "invalid_selector", {}
    raw_labels = selector.get("entry_labels")
    if not isinstance(raw_labels, (list, tuple)) or not raw_labels:
        return [], "invalid_selector", {}
    entry_labels = tuple(_compact_normalized_text(str(label or "")) for label in raw_labels)
    if column_index < 1 or not entry_labels or any(not label for label in entry_labels):
        return [], "invalid_selector", {}

    tables, carrier_detail = uk_table_selector_tables(node, selector)
    if len(tables) != 1:
        return [], "table_not_unique", {"table_count": len(tables), **carrier_detail}

    matches_by_label: dict[str, list[tuple[UKMutableNode, str]]] = {label: [] for label in entry_labels}
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
            if not matches_by_label[label] or matches_by_label[label][-1][0] is not target_cell:
                matches_by_label[label].append((target_cell, row_preview))

    missing = tuple(label for label, matches in matches_by_label.items() if not matches)
    ambiguous = tuple(label for label, matches in matches_by_label.items() if len(matches) > 1)
    if missing or ambiguous:
        return [], "entry_cells_not_unique", {
            "missing_entry_labels": missing,
            "ambiguous_entry_labels": ambiguous,
            "matching_rows": tuple(
                row_preview
                for matches in matches_by_label.values()
                for _cell, row_preview in matches[:2]
            )[:5],
            **carrier_detail,
        }
    return [matches_by_label[label][0][0] for label in entry_labels], "", {
        "matching_cell_count": len(entry_labels),
        "matched_rows": tuple(matches_by_label[label][0][1] for label in entry_labels),
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
            cell_norm = _compact_normalized_text(cell.text or "")
            if selector_mode == "unique_table_text":
                if cell_norm != match_norm:
                    continue
            elif cell_norm.find(match_norm) < 0:
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


def resolve_uk_table_entry_inline_cell(
    node: UKMutableNode,
    selector: dict[str, Any],
) -> tuple[UKMutableNode | None, str, dict[str, Any]]:
    """Resolve a source-owned "nth entry in column N relating to X" table cell."""
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
        return None, "invalid_selector", {}
    relating_norm = _compact_normalized_text(str(selector.get("relating_text") or ""))
    if column_index < 1 or entry_index < 1 or not relating_norm:
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
        return None, "entry_not_found", {
            "matching_entry_count": len(matching_cells),
            "matching_rows": tuple(matching_rows[:5]),
            **carrier_detail,
        }
    return matching_cells[entry_index - 1], "", {
        "matching_entry_count": len(matching_cells),
        "matched_row": matching_rows[entry_index - 1] if entry_index - 1 < len(matching_rows) else "",
        **carrier_detail,
    }

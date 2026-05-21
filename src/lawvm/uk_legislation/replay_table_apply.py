from __future__ import annotations

import re
from dataclasses import replace as dc_replace
from typing import Any, cast

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.addressing import _action_name, _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_replace_children
from lawvm.uk_legislation.replay_records import _append_uk_replay_adjudication
from lawvm.uk_legislation.replay_table_geometry import (
    resolve_uk_table_entry_row_insert_index,
    strip_uk_identity_attrs_recursive,
    uk_table_column_insert_plans,
    uk_table_column_payload_cells,
    uk_table_selector_tables,
)
from lawvm.uk_legislation.source_table_entry_paragraph import (
    SOURCE_TABLE_CELL_PARAGRAPH_SENTINEL_RE as _TABLE_CELL_PARAGRAPH_SENTINEL_RE,
)
from lawvm.uk_legislation.table_selectors import (
    UK_TABLE_COLUMN_INSERT_RULE_ID as _UK_TABLE_COLUMN_INSERT_RULE_ID,
    UK_TABLE_ENTRY_ROW_INSERT_RULE_ID as _UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
)
from lawvm.uk_legislation.uk_grafter import _clean_num


_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_table_entry_row_insert_unresolved"
)
_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_table_column_insert_unresolved"
)


class UKReplayTableApplyMixin:
    def _insert_table_column(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        node, _, _ = self._find_node_by_target(target)
        if node is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve the table-column insertion containing target.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "target_not_found",
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        try:
            after_column_index = int(selector.get("after_column_index") or 0)
            before_column_index = int(selector.get("before_column_index") or 0)
        except (TypeError, ValueError):
            after_column_index = 0
            before_column_index = 0
        if after_column_index < 1 or before_column_index != after_column_index + 1:
            reason = "invalid_selector"
            detail: dict[str, Any] = {}
            table = None
        else:
            tables, carrier_detail = uk_table_selector_tables(node, selector)
            table = tables[0] if len(tables) == 1 else None
            reason = "" if table is not None else "table_not_unique"
            detail = {"table_count": len(tables), **carrier_detail} if table is None else carrier_detail
        payload_cells, payload_reason, payload_detail = uk_table_column_payload_cells(new_node)
        if table is None or payload_reason:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve a source-owned table column insertion.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": payload_reason or reason,
                    **detail,
                    **payload_detail,
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False

        payload_index = 0
        adjusted_spans = 0
        inserted_cells = 0
        matched_rows: list[str] = []
        plans = uk_table_column_insert_plans(table)
        for plan in plans:
            row = cast(UKMutableNode, plan["row"])
            row_cells = cast(dict[int, UKMutableNode], plan["row_cells"])
            owned_ranges = cast(list[tuple[int, int, UKMutableNode, int]], plan["owned_ranges"])
            before_cell = row_cells.get(before_column_index)
            after_cell = row_cells.get(after_column_index)
            owned_spanners = [
                (start, end, cell, physical_index)
                for start, end, cell, physical_index in owned_ranges
                if start <= after_column_index and end >= before_column_index
            ]
            if owned_spanners:
                if len(owned_spanners) != 1:
                    reason = "column_boundary_span_ambiguous"
                    break
                _start, _end, spanner, _physical_index = owned_spanners[0]
                old_colspan_raw = str(spanner.attrs.get("colspan") or "1")
                if not re.fullmatch(r"[0-9]+", old_colspan_raw):
                    reason = "unsupported_colspan_value"
                    break
                old_colspan = int(old_colspan_raw)
                spanner.attrs = {**spanner.attrs, "colspan": str(old_colspan + 1)}
                adjusted_spans += 1
                matched_rows.append(str(spanner.text or "")[:160])
                continue
            if before_cell is not None and before_cell is after_cell:
                reason = "column_boundary_carried_span_unsupported"
                break
            if payload_index >= len(payload_cells):
                reason = "payload_row_count_too_small"
                break
            insertion_candidates = [
                physical_index
                for start, _end, _cell, physical_index in owned_ranges
                if start >= before_column_index
            ]
            if insertion_candidates:
                insert_index = min(insertion_candidates)
            elif row_cells and max(row_cells) == after_column_index:
                insert_index = len(row.children)
            else:
                reason = "column_boundary_not_found"
                break
            row.children[insert_index:insert_index] = [payload_cells[payload_index]]
            inserted_cells += 1
            payload_index += 1
            matched_rows.append(
                " | ".join(
                    str(row_cells[col].text or "")
                    for col in sorted(row_cells)
                    if str(row_cells[col].text or "")
                )[:160]
            )
        else:
            reason = ""
        if reason or payload_index != len(payload_cells):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not prove the table-column insertion boundary.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": reason or "payload_row_count_too_large",
                    "payload_row_count": len(payload_cells),
                    "payload_rows_consumed": payload_index,
                    "adjusted_spans": adjusted_spans,
                    "inserted_cells": inserted_cells,
                    "matched_rows": tuple(matched_rows[:5]),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_TABLE_COLUMN_INSERT_RULE_ID,
            message=(
                "UK replay inserted a table column after resolving a source-owned "
                "between-columns selector."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "payload_row_count": len(payload_cells),
                "adjusted_spans": adjusted_spans,
                "inserted_cells": inserted_cells,
                "matched_rows": tuple(matched_rows[:5]),
                "family": "source_table_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _insert_table_entry_row(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        node, _, _ = self._find_node_by_target(target)
        if node is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve the table-row insertion containing target.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "target_not_found",
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        table, insert_index, reason, detail = resolve_uk_table_entry_row_insert_index(node, selector)
        if table is None or insert_index is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay could not resolve a unique source-owned table row "
                    "for table-row insertion."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": reason,
                    **detail,
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        payload_kind = _uk_kind_value(new_node.kind).lower()
        if payload_kind not in {"row", "table"}:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay table-row insertion payload was not a table row.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "payload_not_row",
                    "payload_kind": _uk_kind_value(new_node.kind),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        inserted_rows = [new_node] if payload_kind == "row" else [
            child for child in new_node.children if _uk_kind_value(child.kind).lower() == "row"
        ]
        if not inserted_rows:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay table-row insertion payload had no table rows.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "payload_has_no_rows",
                    "payload_kind": _uk_kind_value(new_node.kind),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        for row in inserted_rows:
            strip_uk_identity_attrs_recursive(row)
        children = list(table.children)
        children[insert_index:insert_index] = inserted_rows
        uk_replace_children(table, children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
            message=(
                "UK replay inserted a table row after resolving an explicit "
                "table-entry selector."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "insert_index": insert_index,
                "inserted_row_count": len(inserted_rows),
                **detail,
                "family": "source_table_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _apply_source_carried_table_cell_paragraph_substitution(
        self,
        cell: UKMutableNode,
        match_text: str,
        replacement: str,
    ) -> tuple[UKMutableNode, bool, str, dict[str, Any]]:
        match = _TABLE_CELL_PARAGRAPH_SENTINEL_RE.match(match_text)
        if match is None:
            return cell, False, "not_source_carried_table_cell_selector", {}
        text = cell.text or ""
        paragraph_label = _clean_num(match.group("paragraph"))
        subparagraph_label = _clean_num(match.group("subparagraph") or "")
        try:
            paragraph_index = int(paragraph_label) - 1
        except ValueError:
            return cell, False, "invalid_paragraph_label", {"source_paragraph_label": paragraph_label}
        parts = re.split(r"(\n{6,})", text)
        paragraph_slots = [index for index in range(0, len(parts), 2)]
        if paragraph_index < 0 or paragraph_index >= len(paragraph_slots):
            return cell, False, "paragraph_not_found", {
                "source_paragraph_label": paragraph_label,
                "paragraph_count": len(paragraph_slots),
            }
        slot = paragraph_slots[paragraph_index]
        old_paragraph = parts[slot]
        if not subparagraph_label:
            parts[slot] = replacement
            old_fragment = old_paragraph
        else:
            if len(subparagraph_label) != 1 or not subparagraph_label.isalpha():
                return cell, False, "unsupported_subparagraph_label", {
                    "source_paragraph_label": paragraph_label,
                    "source_subparagraph_label": subparagraph_label,
                }
            sub_index = ord(subparagraph_label.lower()) - ord("a")
            if sub_index < 0:
                return cell, False, "invalid_subparagraph_label", {
                    "source_paragraph_label": paragraph_label,
                    "source_subparagraph_label": subparagraph_label,
                }
            if sub_index == 0:
                sub_match = re.search(r"(?P<prefix>[—-]\s*\n+)(?P<old>.*?)(?=\n{4,}|$)", old_paragraph, re.S)
                if sub_match is None:
                    return cell, False, "subparagraph_not_found", {
                        "source_paragraph_label": paragraph_label,
                        "source_subparagraph_label": subparagraph_label,
                    }
                old_fragment = sub_match.group("old")
                parts[slot] = (
                    old_paragraph[: sub_match.start("old")]
                    + replacement
                    + old_paragraph[sub_match.end("old") :]
                )
            else:
                subparts = re.split(r"(\n{4,})", old_paragraph)
                sub_slots = [index for index in range(2, len(subparts), 2)]
                if sub_index - 1 < 0 or sub_index - 1 >= len(sub_slots):
                    return cell, False, "subparagraph_not_found", {
                        "source_paragraph_label": paragraph_label,
                        "source_subparagraph_label": subparagraph_label,
                        "subparagraph_count": len(sub_slots) + 1,
                    }
                sub_slot = sub_slots[sub_index - 1]
                old_fragment = subparts[sub_slot]
                subparts[sub_slot] = replacement
                parts[slot] = "".join(subparts)
        new_text = "".join(parts)
        if new_text == text:
            return cell, False, "replacement_noop", {
                "source_paragraph_label": paragraph_label,
                "source_subparagraph_label": subparagraph_label,
            }
        old_cell = cell
        cell = dc_replace(cell, text=new_text)
        self._replace_node_in_statute(old_cell, cell)
        return cell, True, "", {
            "source_paragraph_label": paragraph_label,
            "source_subparagraph_label": subparagraph_label,
            "old_fragment": " ".join(old_fragment.split())[:240],
            "replacement_fragment": " ".join(replacement.split())[:240],
        }

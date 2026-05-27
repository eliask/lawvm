from __future__ import annotations

import re
from dataclasses import replace as dc_replace
from typing import Any, NamedTuple

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_replace_children
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.replay_table_geometry import (
    resolve_uk_table_entry_row_replace_span,
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
    UK_TABLE_ENTRY_ROW_REPLACE_RULE_ID as _UK_TABLE_ENTRY_ROW_REPLACE_RULE_ID,
)
from lawvm.uk_legislation.uk_grafter import _clean_num


_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_table_entry_row_insert_unresolved"
)
_UK_REPLAY_TABLE_ENTRY_ROW_REPLACE_UNRESOLVED_RULE_ID = (
    "uk_replay_table_entry_row_replace_unresolved"
)
_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_table_column_insert_unresolved"
)
_UNSIGNED_INT_RE = re.compile(r"[0-9]+")
_TABLE_CELL_PARAGRAPH_SPLIT_RE = re.compile(r"(\n{6,})")
_TABLE_CELL_FIRST_SUBPARAGRAPH_RE = re.compile(
    r"(?P<prefix>[—-]\s*\n+)(?P<old>.*?)(?=\n{4,}|$)",
    re.S,
)
_TABLE_CELL_SUBPARAGRAPH_SPLIT_RE = re.compile(r"(\n{4,})")


class _TableCellParagraphSubstitutionResult(NamedTuple):
    cell: UKMutableNode
    applied: bool
    reason_code: str
    detail: dict[str, Any]


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
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="target_not_found",
                    family="source_table_elaboration",
                ),
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
        payload_cells_result = uk_table_column_payload_cells(new_node)
        if table is None or payload_cells_result.reason_code:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve a source-owned table column insertion.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code=payload_cells_result.reason_code or reason,
                    **detail,
                    **payload_cells_result.detail,
                    family="source_table_elaboration",
                ),
            )
            return False

        payload_index = 0
        adjusted_spans = 0
        inserted_cells = 0
        matched_rows: list[str] = []
        plans = uk_table_column_insert_plans(table)
        for plan in plans:
            row = plan.row
            row_cells = plan.row_cells
            owned_ranges = plan.owned_ranges
            before_cell = row_cells.get(before_column_index)
            after_cell = row_cells.get(after_column_index)
            owned_spanners = [
                owned_range
                for owned_range in owned_ranges
                if owned_range.start_col <= after_column_index
                and owned_range.end_col >= before_column_index
            ]
            if owned_spanners:
                if len(owned_spanners) != 1:
                    reason = "column_boundary_span_ambiguous"
                    break
                spanner = owned_spanners[0].cell
                old_colspan_raw = str(spanner.attrs.get("colspan") or "1")
                if not _UNSIGNED_INT_RE.fullmatch(old_colspan_raw):
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
            if payload_index >= len(payload_cells_result.cells):
                reason = "payload_row_count_too_small"
                break
            insertion_candidates = [
                owned_range.physical_index
                for owned_range in owned_ranges
                if owned_range.start_col >= before_column_index
            ]
            if insertion_candidates:
                insert_index = min(insertion_candidates)
            elif row_cells and max(row_cells) == after_column_index:
                insert_index = len(row.children)
            else:
                reason = "column_boundary_not_found"
                break
            row.children[insert_index:insert_index] = [
                payload_cells_result.cells[payload_index]
            ]
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
        if reason or payload_index != len(payload_cells_result.cells):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_COLUMN_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not prove the table-column insertion boundary.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code=reason or "payload_row_count_too_large",
                    payload_row_count=len(payload_cells_result.cells),
                    payload_rows_consumed=payload_index,
                    adjusted_spans=adjusted_spans,
                    inserted_cells=inserted_cells,
                    matched_rows=tuple(matched_rows[:5]),
                    family="source_table_elaboration",
                ),
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
            detail=uk_replay_action_target_detail(
                op,
                target,
                blocking=False,
                selector=dict(selector),
                payload_row_count=len(payload_cells_result.cells),
                adjusted_spans=adjusted_spans,
                inserted_cells=inserted_cells,
                matched_rows=tuple(matched_rows[:5]),
                family="source_table_elaboration",
            ),
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
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="target_not_found",
                    family="source_table_elaboration",
                ),
            )
            return False
        row_insert = resolve_uk_table_entry_row_insert_index(node, selector)
        if row_insert.table is None or row_insert.insert_index is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay could not resolve a unique source-owned table row "
                    "for table-row insertion."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code=row_insert.reason_code,
                    **row_insert.detail,
                    family="source_table_elaboration",
                ),
            )
            return False
        payload_kind = _uk_kind_value(new_node.kind).lower()
        if payload_kind not in {"row", "table"}:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay table-row insertion payload was not a table row.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="payload_not_row",
                    payload_kind=_uk_kind_value(new_node.kind),
                    family="source_table_elaboration",
                ),
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
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="payload_has_no_rows",
                    payload_kind=_uk_kind_value(new_node.kind),
                    family="source_table_elaboration",
                ),
            )
            return False
        if str(selector.get("source_payload_mode") or "") == "each_column_entry_text":
            table_column_count = int(row_insert.detail.get("table_column_count") or 0)
            if table_column_count < 1 or len(inserted_rows) != 1 or len(inserted_rows[0].children) != 1:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_TABLE_ENTRY_ROW_INSERT_UNRESOLVED_RULE_ID,
                    message=(
                        "UK replay could not expand an each-column table-row "
                        "insertion from a single source-owned cell."
                    ),
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(
                        op,
                        target,
                        selector=dict(selector),
                        reason_code="each_column_payload_not_single_cell",
                        table_column_count=table_column_count,
                        payload_row_count=len(inserted_rows),
                        payload_cell_count=len(inserted_rows[0].children) if inserted_rows else 0,
                        family="source_table_elaboration",
                    ),
                )
                return False
            source_cell = inserted_rows[0].children[0]
            expanded_cells: list[UKMutableNode] = []
            for column_index in range(1, table_column_count + 1):
                cell = UKMutableNode.from_irnode(source_cell.to_irnode())
                cell.attrs = {**dict(cell.attrs), "column_index": str(column_index)}
                expanded_cells.append(cell)
            inserted_rows = [
                UKMutableNode(
                    kind=inserted_rows[0].kind,
                    label=inserted_rows[0].label,
                    text=inserted_rows[0].text,
                    attrs={**dict(inserted_rows[0].attrs), "expanded_column_count": str(table_column_count)},
                    children=expanded_cells,
                )
            ]
        for row in inserted_rows:
            strip_uk_identity_attrs_recursive(row)
        children = list(row_insert.table.children)
        children[row_insert.insert_index:row_insert.insert_index] = inserted_rows
        uk_replace_children(row_insert.table, children)
        self._clear_eid_lookup_index()
        self._note_structure_mutation()
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_TABLE_ENTRY_ROW_INSERT_RULE_ID,
            message=(
                "UK replay inserted a table row after resolving an explicit "
                "table-entry selector."
            ),
            op=op,
            detail=uk_replay_action_target_detail(
                op,
                target,
                blocking=False,
                selector=dict(selector),
                insert_index=row_insert.insert_index,
                inserted_row_count=len(inserted_rows),
                **row_insert.detail,
                family="source_table_elaboration",
            ),
        )
        return True

    def _replace_table_entry_rows(
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
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_REPLACE_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve the table-row replacement containing target.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="target_not_found",
                    family="source_table_elaboration",
                ),
            )
            return False
        table, start, end, reason, detail = resolve_uk_table_entry_row_replace_span(node, selector)
        if table is None or start is None or end is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_REPLACE_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay could not resolve a unique source-owned table-row "
                    "span for table-entry replacement."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code=reason,
                    **detail,
                    family="source_table_elaboration",
                ),
            )
            return False
        payload_kind = _uk_kind_value(new_node.kind).lower()
        replacement_rows = [new_node] if payload_kind == "row" else [
            child for child in new_node.children if _uk_kind_value(child.kind).lower() == "row"
        ]
        if payload_kind not in {"row", "table"} or not replacement_rows:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_ENTRY_ROW_REPLACE_UNRESOLVED_RULE_ID,
                message="UK replay table-row replacement payload had no table rows.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="payload_has_no_rows",
                    payload_kind=_uk_kind_value(new_node.kind),
                    family="source_table_elaboration",
                ),
            )
            return False
        for row in replacement_rows:
            strip_uk_identity_attrs_recursive(row)
        children = list(table.children)
        replaced_row_count = end - start
        children[start:end] = replacement_rows
        uk_replace_children(table, children)
        self._clear_eid_lookup_index()
        self._note_structure_mutation()
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_TABLE_ENTRY_ROW_REPLACE_RULE_ID,
            message=(
                "UK replay replaced table rows after resolving explicit "
                "table-entry relating selectors."
            ),
            op=op,
            detail=uk_replay_action_target_detail(
                op,
                target,
                blocking=False,
                selector=dict(selector),
                replaced_row_count=replaced_row_count,
                replacement_row_count=len(replacement_rows),
                **detail,
                family="source_table_elaboration",
            ),
        )
        return True

    def _apply_source_carried_table_cell_paragraph_substitution(
        self,
        cell: UKMutableNode,
        match_text: str,
        replacement: str,
    ) -> _TableCellParagraphSubstitutionResult:
        match = _TABLE_CELL_PARAGRAPH_SENTINEL_RE.match(match_text)
        if match is None:
            return _TableCellParagraphSubstitutionResult(
                cell,
                False,
                "not_source_carried_table_cell_selector",
                {},
            )
        text = cell.text or ""
        paragraph_label = _clean_num(match.group("paragraph"))
        subparagraph_label = _clean_num(match.group("subparagraph") or "")
        try:
            paragraph_index = int(paragraph_label) - 1
        except ValueError:
            return _TableCellParagraphSubstitutionResult(
                cell,
                False,
                "invalid_paragraph_label",
                {"source_paragraph_label": paragraph_label},
            )
        parts = _TABLE_CELL_PARAGRAPH_SPLIT_RE.split(text)
        paragraph_slots = [index for index in range(0, len(parts), 2)]
        if paragraph_index < 0 or paragraph_index >= len(paragraph_slots):
            return _TableCellParagraphSubstitutionResult(
                cell,
                False,
                "paragraph_not_found",
                {
                    "source_paragraph_label": paragraph_label,
                    "paragraph_count": len(paragraph_slots),
                },
            )
        slot = paragraph_slots[paragraph_index]
        old_paragraph = parts[slot]
        if not subparagraph_label:
            parts[slot] = replacement
            old_fragment = old_paragraph
        else:
            if len(subparagraph_label) != 1 or not subparagraph_label.isalpha():
                return _TableCellParagraphSubstitutionResult(
                    cell,
                    False,
                    "unsupported_subparagraph_label",
                    {
                        "source_paragraph_label": paragraph_label,
                        "source_subparagraph_label": subparagraph_label,
                    },
                )
            sub_index = ord(subparagraph_label.lower()) - ord("a")
            if sub_index < 0:
                return _TableCellParagraphSubstitutionResult(
                    cell,
                    False,
                    "invalid_subparagraph_label",
                    {
                        "source_paragraph_label": paragraph_label,
                        "source_subparagraph_label": subparagraph_label,
                    },
                )
            if sub_index == 0:
                sub_match = _TABLE_CELL_FIRST_SUBPARAGRAPH_RE.search(old_paragraph)
                if sub_match is None:
                    return _TableCellParagraphSubstitutionResult(
                        cell,
                        False,
                        "subparagraph_not_found",
                        {
                            "source_paragraph_label": paragraph_label,
                            "source_subparagraph_label": subparagraph_label,
                        },
                    )
                old_fragment = sub_match.group("old")
                parts[slot] = (
                    old_paragraph[: sub_match.start("old")]
                    + replacement
                    + old_paragraph[sub_match.end("old") :]
                )
            else:
                subparts = _TABLE_CELL_SUBPARAGRAPH_SPLIT_RE.split(old_paragraph)
                sub_slots = [index for index in range(2, len(subparts), 2)]
                if sub_index - 1 < 0 or sub_index - 1 >= len(sub_slots):
                    return _TableCellParagraphSubstitutionResult(
                        cell,
                        False,
                        "subparagraph_not_found",
                        {
                            "source_paragraph_label": paragraph_label,
                            "source_subparagraph_label": subparagraph_label,
                            "subparagraph_count": len(sub_slots) + 1,
                        },
                    )
                sub_slot = sub_slots[sub_index - 1]
                old_fragment = subparts[sub_slot]
                subparts[sub_slot] = replacement
                parts[slot] = "".join(subparts)
        new_text = "".join(parts)
        if new_text == text:
            return _TableCellParagraphSubstitutionResult(
                cell,
                False,
                "replacement_noop",
                {
                    "source_paragraph_label": paragraph_label,
                    "source_subparagraph_label": subparagraph_label,
                },
            )
        old_cell = cell
        cell = dc_replace(cell, text=new_text)
        self._replace_node_in_statute(old_cell, cell)
        return _TableCellParagraphSubstitutionResult(
            cell,
            True,
            "",
            {
                "source_paragraph_label": paragraph_label,
                "source_subparagraph_label": subparagraph_label,
                "old_fragment": " ".join(old_fragment.split())[:240],
                "replacement_fragment": " ".join(replacement.split())[:240],
            },
        )

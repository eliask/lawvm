from __future__ import annotations

import json
import re
from dataclasses import replace as dc_replace
from typing import Any, NamedTuple, Protocol

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_replace_children
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.replay_state import NodeLookupResult
from lawvm.uk_legislation.replay_table_geometry import (
    UKExpandedTableRow,
    expanded_uk_table_rows_with_physical_index,
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
_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_table_cell_child_list_insert_unresolved"
)
_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_RESOLVED_RULE_ID = (
    "uk_replay_table_cell_child_list_insert_resolved"
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


class _PendingTableColumnSpanAttrs(NamedTuple):
    spanner: UKMutableNode
    attrs: dict[str, Any]


class _PendingTableColumnCellInsertion(NamedTuple):
    row: UKMutableNode
    insert_index: int
    cell: UKMutableNode


class _TableReplaySelf(Protocol):
    adjudications_out: list[CompileAdjudication]

    def _find_node_by_target(
        self,
        target: LegalAddress,
        *,
        allow_compound_subsection_alias: bool = False,
        allow_recursive_match: bool = True,
        target_resolution_op: LegalOperation | None = None,
    ) -> NodeLookupResult: ...

    def _clear_eid_lookup_index(self) -> None: ...

    def _note_structure_mutation(self) -> None: ...

    def _replace_node_in_statute(self, old_node: UKMutableNode, new_node: UKMutableNode) -> bool: ...

    def _record_children_splice_mutation_event(
        self,
        *,
        container: UKMutableNode,
        helper: str,
        outcome: str,
        reason_code: str,
    ) -> None: ...


def _record_table_structure_splice(
    self: _TableReplaySelf,
    *,
    container: UKMutableNode,
    helper: str,
    outcome: str,
    reason_code: str,
) -> None:
    self._clear_eid_lookup_index()
    self._note_structure_mutation()
    self._record_children_splice_mutation_event(
        container=container,
        helper=helper,
        outcome=outcome,
        reason_code=reason_code,
    )


def _table_cell_ordered_list_units(cell: UKMutableNode) -> list[dict[str, str]]:
    raw = cell.attrs.get("source_ordered_list_units_json")
    if not isinstance(raw, str) or not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    units: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            return []
        label = str(item.get("label") or "").strip()
        text = str(item.get("text") or "").strip()
        if not label or not text:
            return []
        units.append(
            {
                "source_list_type": str(item.get("source_list_type") or ""),
                "source_list_decoration": str(item.get("source_list_decoration") or ""),
                "label": label,
                "text": text,
            }
        )
    return units


def _replace_table_cell_ordered_list_text(
    cell: UKMutableNode,
    old_units: list[dict[str, str]],
    new_units: list[dict[str, str]],
) -> UKMutableNode | None:
    if not old_units:
        return None
    text = str(cell.text or "")
    first_text = old_units[0]["text"]
    last_text = old_units[-1]["text"]
    first_index = text.find(first_text)
    if first_index < 0:
        return None
    last_index = text.rfind(last_text)
    if last_index < first_index:
        return None
    last_end = last_index + len(last_text)
    list_region = text[first_index:last_end]
    separator = "\n\n\n\n" if "\n\n\n\n" in list_region else "\n\n"
    return dc_replace(
        cell,
        text=f"{text[:first_index]}{separator.join(unit['text'] for unit in new_units)}{text[last_end:]}",
        attrs={
            **dict(cell.attrs),
            "source_ordered_list_units_json": json.dumps(
                new_units,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    )


class UKReplayTableApplyMixin:
    def _insert_table_cell_child_list_item(
        self: _TableReplaySelf,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        node, _, _ = self._find_node_by_target(target)
        if node is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve the table-cell child-list containing target.",
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
        table_selection = uk_table_selector_tables(node, selector)
        if len(table_selection.tables) != 1:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve a unique table for table-cell child-list insertion.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="table_not_unique",
                    table_count=len(table_selection.tables),
                    **table_selection.detail,
                    family="source_table_elaboration",
                ),
            )
            return False
        try:
            source_row_number = int(selector.get("source_table_row_number") or 0)
            source_column_index = int(selector.get("source_table_column_index") or 0)
        except (TypeError, ValueError):
            source_row_number = 0
            source_column_index = 0
        anchor_label = _clean_num(str(selector.get("table_child_anchor_label") or ""))
        direction = str(selector.get("table_child_insert_direction") or "")
        if (
            _uk_kind_value(new_node.kind).lower() != "item"
            or not new_node.label
            or not new_node.text
            or source_row_number < 1
            or source_column_index < 1
            or not anchor_label
            or direction not in {"before", "after"}
        ):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay skipped table-cell child-list insertion with an invalid selector or payload.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="invalid_selector_or_payload",
                    payload_kind=_uk_kind_value(new_node.kind),
                    family="source_table_elaboration",
                ),
            )
            return False
        row_matches: list[UKExpandedTableRow] = []
        for expanded_row in expanded_uk_table_rows_with_physical_index(table_selection.tables[0]):
            first_cell = expanded_row.cells_by_column.get(1)
            if first_cell is None:
                continue
            if _clean_num(str(first_cell.text or "")) == str(source_row_number):
                row_matches.append(expanded_row)
        if len(row_matches) != 1:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve a unique source-numbered table row.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="row_number_not_unique",
                    row_match_count=len(row_matches),
                    **table_selection.detail,
                    family="source_table_elaboration",
                ),
            )
            return False
        matched_row = row_matches[0]
        physical_row_index = matched_row.physical_index
        row_cells = matched_row.cells_by_column
        cell = row_cells.get(source_column_index)
        if cell is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve the source-named table column.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="column_not_found",
                    physical_row_index=physical_row_index,
                    **table_selection.detail,
                    family="source_table_elaboration",
                ),
            )
            return False
        old_units = _table_cell_ordered_list_units(cell)
        anchor_indexes = [
            index
            for index, unit in enumerate(old_units)
            if _clean_num(unit["label"]) == anchor_label
        ]
        if len(anchor_indexes) != 1 or any(_clean_num(unit["label"]) == _clean_num(new_node.label) for unit in old_units):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not resolve a unique ordered-list child anchor in the table cell.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="ordered_list_child_anchor_not_unique_or_duplicate_payload",
                    anchor_match_count=len(anchor_indexes),
                    ordered_list_unit_count=len(old_units),
                    physical_row_index=physical_row_index,
                    **table_selection.detail,
                    family="source_table_elaboration",
                ),
            )
            return False
        insert_index = anchor_indexes[0] if direction == "before" else anchor_indexes[0] + 1
        new_units = [
            *old_units[:insert_index],
            {
                "source_list_type": str(new_node.attrs.get("source_list_type") or ""),
                "source_list_decoration": str(new_node.attrs.get("source_list_decoration") or ""),
                "label": str(new_node.label),
                "text": str(new_node.text),
            },
            *old_units[insert_index:],
        ]
        replaced_cell = _replace_table_cell_ordered_list_text(cell, old_units, new_units)
        if replaced_cell is None:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_UNRESOLVED_RULE_ID,
                message="UK replay could not splice the ordered-list text region in the selected table cell.",
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    selector=dict(selector),
                    reason_code="cell_text_units_not_found",
                    physical_row_index=physical_row_index,
                    **table_selection.detail,
                    family="source_table_elaboration",
                ),
            )
            return False
        self._replace_node_in_statute(cell, replaced_cell)
        self._note_structure_mutation()
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_TABLE_CELL_CHILD_LIST_INSERT_RESOLVED_RULE_ID,
            message="UK replay inserted a source-owned ordered-list child into one resolved table cell.",
            op=op,
            detail=uk_replay_action_target_detail(
                op,
                target,
                blocking=False,
                selector=dict(selector),
                reason_code="source_named_row_column_child_anchor_unique",
                physical_row_index=physical_row_index,
                insert_index=insert_index,
                ordered_list_unit_count=len(new_units),
                **table_selection.detail,
                family="source_table_elaboration",
            ),
        )
        return True

    def _insert_table_column(
        self: _TableReplaySelf,
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
            table_selection = uk_table_selector_tables(node, selector)
            tables = table_selection.tables
            carrier_detail = table_selection.detail
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
        pending_span_attrs: list[_PendingTableColumnSpanAttrs] = []
        pending_cell_insertions: list[_PendingTableColumnCellInsertion] = []
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
                pending_span_attrs.append(
                    _PendingTableColumnSpanAttrs(
                        spanner=spanner,
                        attrs={**spanner.attrs, "colspan": str(old_colspan + 1)},
                    )
                )
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
            pending_cell_insertions.append(
                _PendingTableColumnCellInsertion(
                    row=row,
                    insert_index=insert_index,
                    cell=payload_cells_result.cells[payload_index],
                )
            )
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
                    adjusted_spans=0,
                    inserted_cells=0,
                    planned_adjusted_spans=adjusted_spans,
                    planned_inserted_cells=inserted_cells,
                    partial_mutation_applied=False,
                    matched_rows=tuple(matched_rows[:5]),
                    family="source_table_elaboration",
                ),
            )
            return False
        for span_attrs in pending_span_attrs:
            span_attrs.spanner.attrs = span_attrs.attrs
        for insertion in pending_cell_insertions:
            insertion.row.children[insertion.insert_index : insertion.insert_index] = [
                insertion.cell
            ]
        _record_table_structure_splice(
            self,
            container=table,
            helper="_insert_table_column",
            outcome="table_column_inserted",
            reason_code="source_owned_between_columns_selector",
        )
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
        self: _TableReplaySelf,
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
        _record_table_structure_splice(
            self,
            container=row_insert.table,
            helper="_insert_table_entry_row",
            outcome="table_rows_inserted",
            reason_code="source_owned_table_entry_selector",
        )
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
        self: _TableReplaySelf,
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
        row_span = resolve_uk_table_entry_row_replace_span(node, selector)
        if row_span.table is None or row_span.start_index is None or row_span.end_index is None:
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
                    reason_code=row_span.reason_code,
                    **row_span.detail,
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
        children = list(row_span.table.children)
        replaced_row_count = row_span.end_index - row_span.start_index
        children[row_span.start_index:row_span.end_index] = replacement_rows
        uk_replace_children(row_span.table, children)
        _record_table_structure_splice(
            self,
            container=row_span.table,
            helper="_replace_table_entry_rows",
            outcome="table_rows_replaced",
            reason_code="source_owned_table_entry_selector",
        )
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
                **row_span.detail,
                family="source_table_elaboration",
            ),
        )
        return True

    def _apply_source_carried_table_cell_paragraph_substitution(
        self: _TableReplaySelf,
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

"""UK replay helpers for source-owned schedule-list entries.

This module owns replay-time application for UK schedule-list-entry effects and
related table-backed schedule entry rows. The methods emit existing replay
adjudications and do not change legal semantics relative to the monolithic
executor implementation.
"""

from __future__ import annotations

from typing import Any

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _action_name, _uk_kind_value
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_replace_children
from lawvm.uk_legislation.replay_records import _append_uk_replay_adjudication
from lawvm.uk_legislation.replay_table_geometry import (
    expanded_uk_table_rows_with_physical_index,
    strip_uk_identity_attrs_recursive,
)
from lawvm.uk_legislation.replay_text import (
    _compact_normalized_text,
    _compact_numbered_schedule_entry_text,
    _compact_numbered_schedule_entry_text_without_article,
    _compact_schedule_entry_anchor_with_citation_short_title,
    _compact_schedule_entry_anchor_without_article,
    _schedule_entry_parenthetical_paragraph_anchor,
)
from lawvm.uk_legislation.uk_grafter import _clean_num


_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_anchor_unresolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_repeal_unresolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_RESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_repeal_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_replace_unresolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_RESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_replace_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PREFIX_NORMALIZED_RULE_ID = (
    "uk_replay_schedule_list_entry_anchor_prefix_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_ARTICLE_NORMALIZED_RULE_ID = (
    "uk_replay_schedule_list_entry_anchor_article_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PARENTHETICAL_PARAGRAPH_RULE_ID = (
    "uk_replay_schedule_list_entry_anchor_parenthetical_paragraph_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_GROUP_ANCHOR_RULE_ID = (
    "uk_replay_schedule_list_entry_group_anchor_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_ALPHABETICAL_POSITION_RULE_ID = (
    "uk_replay_schedule_list_entry_alphabetical_position_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ROWS_INSERT_RESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_table_rows_insert_resolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ROWS_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_list_entry_table_rows_insert_unresolved"
)
_UK_REPLAY_SCHEDULE_TABLE_END_ROWS_INSERT_RESOLVED_RULE_ID = (
    "uk_replay_schedule_table_end_rows_insert_resolved"
)
_UK_REPLAY_SCHEDULE_TABLE_END_ROWS_INSERT_UNRESOLVED_RULE_ID = (
    "uk_replay_schedule_table_end_rows_insert_unresolved"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ANCHOR_CITATION_SHORT_TITLE_RULE_ID = (
    "uk_replay_schedule_list_entry_table_anchor_citation_short_title_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_PARENTHETICAL_PARAGRAPH_RULE_ID = (
    "uk_replay_schedule_list_entry_repeal_parenthetical_paragraph_normalized"
)
_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_NUMBERED_ANCHOR_RULE_ID = (
    "uk_replay_schedule_list_entry_repeal_numbered_anchor_normalized"
)


class UKReplayScheduleListApplyMixin:
    adjudications_out: list[CompileAdjudication]

    def _insert_schedule_list_entry_table_rows(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        direction = str(selector.get("direction") or "")
        is_end_insert = direction == "end"
        unresolved_rule_id = (
            _UK_REPLAY_SCHEDULE_TABLE_END_ROWS_INSERT_UNRESOLVED_RULE_ID
            if is_end_insert
            else _UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ROWS_INSERT_UNRESOLVED_RULE_ID
        )
        resolved_rule_id = (
            _UK_REPLAY_SCHEDULE_TABLE_END_ROWS_INSERT_RESOLVED_RULE_ID
            if is_end_insert
            else _UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ROWS_INSERT_RESOLVED_RULE_ID
        )
        if _uk_kind_value(new_node.kind) != "table":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message="UK replay skipped schedule-list table-row insert: payload was not a table.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "payload_not_table",
                    "payload_kind": _uk_kind_value(new_node.kind),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        schedule_node, _, _ = self._find_node_by_target(target)
        if schedule_node is None or _uk_kind_value(schedule_node.kind) != "schedule":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message=(
                    "UK replay skipped schedule-list table-row insert: target "
                    "did not resolve to a schedule carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_target_unresolved",
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        direct_tables = [
            child
            for child in schedule_node.children
            if _uk_kind_value(child.kind) == "table"
        ]
        direct_entries = [
            child
            for child in schedule_node.children
            if _uk_kind_value(child.kind) == "schedule_entry"
        ]
        if len(direct_tables) != 1 or direct_entries:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message=(
                    "UK replay skipped schedule-list table-row insert: schedule "
                    "was not represented by exactly one direct table and no "
                    "direct schedule-entry children."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_not_single_table_backed",
                    "table_count": len(direct_tables),
                    "direct_entry_count": len(direct_entries),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        anchor_text = str(selector.get("anchor_text") or "")
        anchor_norm = _compact_normalized_text(anchor_text)
        article_anchor_norm = _compact_schedule_entry_anchor_without_article(anchor_text)
        citation_short_anchor_norm = _compact_schedule_entry_anchor_with_citation_short_title(anchor_text)
        if direction not in {"before", "after", "end"} or (not is_end_insert and not anchor_norm):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message="UK replay skipped schedule-list table-row insert: selector was invalid.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "invalid_selector",
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        table = direct_tables[0]
        payload_rows = [
            child
            for child in new_node.children
            if _uk_kind_value(child.kind) == "row"
        ]
        if is_end_insert:
            if not payload_rows:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=unresolved_rule_id,
                    message=(
                        "UK replay skipped schedule table end-row insert: "
                        "payload rows were absent."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(selector),
                        "reason_code": "payload_empty",
                        "payload_row_count": len(payload_rows),
                        "family": "source_table_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return False
            insert_index = len(table.children)
            for row in payload_rows:
                strip_uk_identity_attrs_recursive(row)
            children = list(table.children)
            children[insert_index:insert_index] = payload_rows
            uk_replace_children(table, children)
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=resolved_rule_id,
                message=(
                    "UK replay inserted source-owned schedule table rows at "
                    "the end of the unique table-backed schedule carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "explicit_schedule_end_unique_table",
                    "insert_index": insert_index,
                    "payload_row_count": len(payload_rows),
                    "family": "source_table_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
            return True
        matched_rows: list[tuple[int, str, str]] = []
        last_anchor_cell: UKMutableNode | None = None
        for row_index, row_cells in expanded_uk_table_rows_with_physical_index(table):
            anchor_cell = row_cells.get(1)
            if anchor_cell is None or anchor_cell is last_anchor_cell:
                continue
            last_anchor_cell = anchor_cell
            cell_text = str(anchor_cell.text or "")
            cell_norm = _compact_normalized_text(cell_text)
            cell_article_norm = _compact_schedule_entry_anchor_without_article(cell_text)
            cell_citation_short_norm = _compact_schedule_entry_anchor_with_citation_short_title(cell_text)
            match_mode = ""
            if cell_norm == anchor_norm:
                match_mode = "exact"
            elif cell_norm.startswith(anchor_norm):
                match_mode = "prefix"
            elif article_anchor_norm and cell_article_norm == article_anchor_norm:
                match_mode = "article"
            elif article_anchor_norm and cell_article_norm.startswith(article_anchor_norm):
                match_mode = "article_prefix"
            elif citation_short_anchor_norm and cell_norm == citation_short_anchor_norm:
                match_mode = "citation_short_title"
            elif citation_short_anchor_norm and cell_norm.startswith(citation_short_anchor_norm):
                match_mode = "citation_short_title_prefix"
            elif cell_citation_short_norm and cell_citation_short_norm == anchor_norm:
                match_mode = "cell_citation_short_title"
            elif cell_citation_short_norm and cell_citation_short_norm.startswith(anchor_norm):
                match_mode = "cell_citation_short_title_prefix"
            if match_mode:
                matched_rows.append((row_index, match_mode, cell_text[:240]))
        if len(matched_rows) != 1 or not payload_rows:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=unresolved_rule_id,
                message=(
                    "UK replay skipped schedule-list table-row insert: anchor "
                    "row did not resolve uniquely or payload rows were absent."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_not_unique_or_payload_empty",
                    "anchor_match_count": len(matched_rows),
                    "payload_row_count": len(payload_rows),
                    "matching_rows": tuple(row[2] for row in matched_rows[:5]),
                    "family": "source_table_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        row_index, match_mode, row_preview = matched_rows[0]
        insert_index = row_index if direction == "before" else row_index + 1
        for row in payload_rows:
            strip_uk_identity_attrs_recursive(row)
        children = list(table.children)
        children[insert_index:insert_index] = payload_rows
        uk_replace_children(table, children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=resolved_rule_id,
            message=(
                "UK replay inserted source-owned schedule table rows after "
                "resolving an explicit schedule-list entry anchor in the table."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "reason_code": "explicit_entry_anchor_unique_in_schedule_table",
                "match_mode": match_mode,
                "matched_row": row_preview,
                "anchor_normalization_rule_id": (
                    _UK_REPLAY_SCHEDULE_LIST_ENTRY_TABLE_ANCHOR_CITATION_SHORT_TITLE_RULE_ID
                    if "citation_short_title" in match_mode
                    else ""
                ),
                "insert_index": insert_index,
                "payload_row_count": len(payload_rows),
                "family": "source_table_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _insert_schedule_list_entry(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        if _uk_kind_value(new_node.kind) != "schedule_entry":
            return False
        carrier_node, _, _ = self._find_node_by_target(target)
        carrier_kind = _uk_kind_value(carrier_node.kind) if carrier_node is not None else ""
        if carrier_node is None or carrier_kind not in {"schedule", "part", "chapter", "division"}:
            if self._target_under_repealed_prefix(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_repealed_target_gap",
                    message=(
                        "UK replay skipped schedule-list-entry insert: carrier "
                        "target was already repealed earlier in the chain."
                    ),
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                        "selector": dict(selector),
                        "reason_code": "schedule_target_previously_repealed",
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return False
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry insert: target did "
                    "not resolve to a schedule or schedule-partition carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_target_unresolved",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        anchor_norm = _compact_normalized_text(str(selector.get("anchor_text") or ""))
        direction = str(selector.get("direction") or "")
        if (not anchor_norm and direction != "alphabetical") or direction not in {"before", "after", "alphabetical"}:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry insert: selector was "
                    "missing a valid anchor or direction."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "invalid_selector",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False

        entry_rows: list[tuple[int, UKMutableNode]] = [
            (idx, child)
            for idx, child in enumerate(carrier_node.children)
            if _uk_kind_value(child.kind) == "schedule_entry"
        ]
        if direction == "alphabetical":
            inserted_sort_key = _compact_schedule_entry_anchor_without_article(new_node.text)
            duplicate_matches = [
                (idx, child)
                for idx, child in entry_rows
                if _compact_schedule_entry_anchor_without_article(child.text) == inserted_sort_key
            ]
            if not inserted_sort_key or duplicate_matches:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID,
                    message=(
                        "UK replay skipped alphabetical schedule-list-entry insert: "
                        "inserted entry text was missing or already present."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(selector),
                        "reason_code": "alphabetical_position_duplicate_or_empty",
                        "entry_count": len(entry_rows),
                        "duplicate_match_count": len(duplicate_matches),
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return False
            insert_index = len(carrier_node.children)
            for idx, child in entry_rows:
                child_sort_key = _compact_schedule_entry_anchor_without_article(child.text)
                if child_sort_key > inserted_sort_key:
                    insert_index = idx
                    break
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ALPHABETICAL_POSITION_RULE_ID,
                message=(
                    "UK replay placed a schedule-list-entry insert using the "
                    "source's explicit alphabetical-order instruction."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "explicit_alphabetical_order",
                    "entry_count": len(entry_rows),
                    "insert_index": insert_index,
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
            for key in ("eId", "id"):
                new_node.attrs.pop(key, None)
            children = list(carrier_node.children)
            children.insert(insert_index, new_node)
            uk_replace_children(carrier_node, children)
            return True

        matches = [
            (idx, child)
            for idx, child in entry_rows
            if _compact_normalized_text(child.text) == anchor_norm
        ]
        prefix_normalized = False
        article_normalized = False
        parenthetical_paragraph_normalized = False
        if not matches:
            matches = [
                (idx, child)
                for idx, child in entry_rows
                if _compact_normalized_text(child.text).startswith(anchor_norm)
            ]
            prefix_normalized = len(matches) == 1
        if not matches:
            article_anchor_norm = _compact_schedule_entry_anchor_without_article(
                str(selector.get("anchor_text") or "")
            )
            matches = [
                (idx, child)
                for idx, child in entry_rows
                if article_anchor_norm
                and (
                    _compact_schedule_entry_anchor_without_article(child.text) == article_anchor_norm
                    or _compact_schedule_entry_anchor_without_article(child.text).startswith(article_anchor_norm)
                )
            ]
            article_normalized = len(matches) == 1
        if not matches and carrier_kind != "schedule":
            parenthetical_anchor = _schedule_entry_parenthetical_paragraph_anchor(
                str(selector.get("anchor_text") or "")
            )
            if parenthetical_anchor is not None:
                entry_text, paragraph_label = parenthetical_anchor
                entry_article_norm = _compact_schedule_entry_anchor_without_article(entry_text)
                matches = [
                    (idx, child)
                    for idx, child in entry_rows
                    if _clean_num(child.label or "") == paragraph_label
                    and entry_article_norm
                    and (
                        _compact_schedule_entry_anchor_without_article(child.text) == entry_article_norm
                        or _compact_schedule_entry_anchor_without_article(child.text).startswith(entry_article_norm)
                    )
                ]
                parenthetical_paragraph_normalized = len(matches) == 1
        if not matches:
            grouped_entry_rows: list[tuple[int, UKMutableNode, int, UKMutableNode]] = [
                (group_idx, group, child_idx, child)
                for group_idx, group in enumerate(carrier_node.children)
                if _uk_kind_value(group.kind) == "p1group"
                for child_idx, child in enumerate(group.children)
                if _uk_kind_value(child.kind) == "schedule_entry"
            ]

            def _matches_in_group(mode: str) -> list[tuple[int, UKMutableNode, int, UKMutableNode]]:
                if mode == "exact":
                    return [
                        row for row in grouped_entry_rows if _compact_normalized_text(row[3].text) == anchor_norm
                    ]
                if mode == "prefix":
                    return [
                        row
                        for row in grouped_entry_rows
                        if _compact_normalized_text(row[3].text).startswith(anchor_norm)
                    ]
                article_anchor_norm = _compact_schedule_entry_anchor_without_article(
                    str(selector.get("anchor_text") or "")
                )
                return [
                    row
                    for row in grouped_entry_rows
                    if article_anchor_norm
                    and (
                        _compact_schedule_entry_anchor_without_article(row[3].text) == article_anchor_norm
                        or _compact_schedule_entry_anchor_without_article(row[3].text).startswith(article_anchor_norm)
                    )
                ]

            grouped_matches: list[tuple[int, UKMutableNode, int, UKMutableNode]] = []
            grouped_match_mode = ""
            for mode in ("exact", "prefix", "article"):
                grouped_matches = _matches_in_group(mode)
                if grouped_matches:
                    grouped_match_mode = mode
                    break
            if len(grouped_matches) == 1:
                group_idx, group_node, child_idx, _child = grouped_matches[0]
                insert_index = child_idx if direction == "before" else child_idx + 1
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_GROUP_ANCHOR_RULE_ID,
                    message=(
                        "UK replay resolved a schedule-list-entry anchor inside "
                        "a schedule child group and inserted into that same group."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(selector),
                        "reason_code": "anchor_unique_in_schedule_child_group",
                        "group_index": group_idx,
                        "group_kind": _uk_kind_value(group_node.kind),
                        "group_label": group_node.label or "",
                        "group_text": (group_node.text or "")[:200],
                        "group_entry_index": child_idx,
                        "group_insert_index": insert_index,
                        "grouped_entry_count": len(grouped_entry_rows),
                        "match_mode": grouped_match_mode,
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": False,
                        "strict_disposition": "record",
                        "quirks_disposition": "record",
                    },
                )
                for key in ("eId", "id"):
                    new_node.attrs.pop(key, None)
                group_children = list(group_node.children)
                group_children.insert(insert_index, new_node)
                group_node.children = group_children
                return True
            matches = [(idx, child) for idx, child in enumerate(grouped_matches)]
        if len(matches) != 1:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry insert: anchor entry "
                    "did not resolve uniquely."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_not_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "grouped_entry_count": len(grouped_entry_rows) if "grouped_entry_rows" in locals() else 0,
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        if article_normalized:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_ARTICLE_NORMALIZED_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry anchor after "
                    "normalizing a leading article."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_leading_article_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
        if prefix_normalized:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PREFIX_NORMALIZED_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry anchor as the "
                    "unique prefix of a longer source entry."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_prefix_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
        if parenthetical_paragraph_normalized:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PARENTHETICAL_PARAGRAPH_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry insert anchor "
                    "after validating its parenthetical paragraph label."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_parenthetical_paragraph_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )

        anchor_index = matches[0][0]
        insert_index = anchor_index if direction == "before" else anchor_index + 1
        for key in ("eId", "id"):
            new_node.attrs.pop(key, None)
        children = list(carrier_node.children)
        children.insert(insert_index, new_node)
        uk_replace_children(carrier_node, children)
        return True

    def _repeal_schedule_list_entries(
        self,
        target: LegalAddress,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        carrier_node, _, _ = self._find_node_by_target(target)
        carrier_kind = _uk_kind_value(carrier_node.kind) if carrier_node is not None else ""
        if carrier_node is None or carrier_kind not in {"schedule", "part", "chapter", "division"}:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry repeal: target did "
                    "not resolve to a schedule or schedule-partition carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_target_unresolved",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        raw_anchors = selector.get("anchors")
        anchors = tuple(str(anchor or "") for anchor in raw_anchors) if isinstance(raw_anchors, list) else ()
        anchors = tuple(anchor for anchor in anchors if _compact_normalized_text(anchor))
        if not anchors:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID,
                message="UK replay skipped schedule-list-entry repeal: selector had no entry anchors.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "invalid_selector",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        entry_rows: list[tuple[UKMutableNode, int, UKMutableNode]] = []
        if carrier_kind == "schedule":
            entry_rows = [
                (carrier_node, idx, child)
                for idx, child in enumerate(carrier_node.children)
                if _uk_kind_value(child.kind) == "schedule_entry"
            ]
        else:
            def _collect_partition_entry_rows(parent: UKMutableNode) -> None:
                for idx, child in enumerate(parent.children):
                    child_kind = _uk_kind_value(child.kind)
                    if child_kind == "paragraph":
                        entry_rows.append((parent, idx, child))
                    elif child_kind in {"p1group", "pblock", "part", "chapter", "division"}:
                        _collect_partition_entry_rows(child)

            _collect_partition_entry_rows(carrier_node)

        def _entry_text_norm(child: UKMutableNode) -> str:
            if carrier_kind == "schedule":
                return _compact_normalized_text(child.text)
            return _compact_numbered_schedule_entry_text(child.text)

        def _entry_text_article_norm(child: UKMutableNode) -> str:
            if carrier_kind == "schedule":
                return _compact_schedule_entry_anchor_without_article(child.text)
            return _compact_numbered_schedule_entry_text_without_article(child.text)

        def _matches_for_anchor(anchor: str) -> tuple[list[tuple[UKMutableNode, int, UKMutableNode]], str]:
            anchor_norm = _compact_normalized_text(anchor)
            matches = [
                (parent, idx, child)
                for parent, idx, child in entry_rows
                if _entry_text_norm(child) == anchor_norm
            ]
            if matches:
                return matches, "exact"
            matches = [
                (parent, idx, child)
                for parent, idx, child in entry_rows
                if _entry_text_norm(child).startswith(anchor_norm)
            ]
            if matches:
                return matches, "prefix"
            article_anchor_norm = _compact_schedule_entry_anchor_without_article(anchor)
            matches = [
                (parent, idx, child)
                for parent, idx, child in entry_rows
                if article_anchor_norm
                and (
                    _entry_text_article_norm(child) == article_anchor_norm
                    or _entry_text_article_norm(child).startswith(article_anchor_norm)
                )
            ]
            if matches:
                return matches, "article"
            if carrier_kind != "schedule":
                parenthetical_anchor = _schedule_entry_parenthetical_paragraph_anchor(anchor)
                if parenthetical_anchor is not None:
                    entry_text, paragraph_label = parenthetical_anchor
                    entry_article_norm = _compact_schedule_entry_anchor_without_article(entry_text)
                    matches = [
                        (parent, idx, child)
                        for parent, idx, child in entry_rows
                        if _clean_num(child.label or "") == paragraph_label
                        and entry_article_norm
                        and (
                            _entry_text_article_norm(child) == entry_article_norm
                            or _entry_text_article_norm(child).startswith(entry_article_norm)
                        )
                    ]
                    if matches:
                        return matches, "parenthetical_paragraph"
                numbered_anchor_norm = _compact_numbered_schedule_entry_text(anchor)
                matches = [
                    (parent, idx, child)
                    for parent, idx, child in entry_rows
                    if numbered_anchor_norm
                    and (
                        _entry_text_norm(child) == numbered_anchor_norm
                        or _entry_text_norm(child).startswith(numbered_anchor_norm)
                    )
                ]
                if matches:
                    return matches, "numbered"
                numbered_anchor_article_norm = _compact_numbered_schedule_entry_text_without_article(anchor)
                matches = [
                    (parent, idx, child)
                    for parent, idx, child in entry_rows
                    if numbered_anchor_article_norm
                    and (
                        _entry_text_article_norm(child) == numbered_anchor_article_norm
                        or _entry_text_article_norm(child).startswith(numbered_anchor_article_norm)
                    )
                ]
                if matches:
                    return matches, "numbered_article"
            return [], "none"

        matched_rows: list[tuple[UKMutableNode, int, UKMutableNode]] = []
        match_modes: dict[str, str] = {}
        for anchor in anchors:
            matches, mode = _matches_for_anchor(anchor)
            if len(matches) != 1:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID,
                    message=(
                        "UK replay skipped schedule-list-entry repeal: entry "
                        "anchor did not resolve uniquely."
                    ),
                    op=op,
                    detail={
                        "target": str(target),
                        "selector": dict(selector),
                        "anchor": anchor,
                        "reason_code": "anchor_not_unique",
                        "anchor_match_count": len(matches),
                        "entry_count": len(entry_rows),
                        "carrier_kind": carrier_kind,
                        "family": "source_schedule_list_entry_elaboration",
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
                return False
            matched_rows.append(matches[0])
            match_modes[anchor] = mode
        matched_keys = tuple((id(parent), idx) for parent, idx, _child in matched_rows)
        if len(set(matched_keys)) != len(matched_keys):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry repeal: multiple "
                    "anchors resolved to the same entry child."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_collision",
                    "matched_indices": tuple(idx for _parent, idx, _child in matched_rows),
                    "carrier_kind": carrier_kind,
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        rows_by_parent: dict[int, tuple[UKMutableNode, list[int]]] = {}
        for parent, idx, _child in matched_rows:
            key = id(parent)
            if key not in rows_by_parent:
                rows_by_parent[key] = (parent, [])
            rows_by_parent[key][1].append(idx)
        for parent, indices in rows_by_parent.values():
            children = list(parent.children)
            for idx in sorted(indices, reverse=True):
                children.pop(idx)
            uk_replace_children(parent, children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_RESOLVED_RULE_ID,
            message=(
                "UK replay applied a schedule-list-entry repeal after every "
                "source entry anchor resolved to exactly one direct schedule child."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "reason_code": "explicit_entry_anchors_unique",
                "matched_indices": tuple(idx for _parent, idx, _child in matched_rows),
                "match_modes": match_modes,
                "normalization_rule_ids": tuple(
                    (
                        _UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_PARENTHETICAL_PARAGRAPH_RULE_ID
                        if mode == "parenthetical_paragraph"
                        else _UK_REPLAY_SCHEDULE_LIST_ENTRY_REPEAL_NUMBERED_ANCHOR_RULE_ID
                    )
                    for mode in match_modes.values()
                    if mode in {"parenthetical_paragraph", "numbered", "numbered_article"}
                ),
                "entry_count": len(entry_rows),
                "deleted_count": len(matched_rows),
                "carrier_kind": carrier_kind,
                "family": "source_schedule_list_entry_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True

    def _replace_schedule_list_entry(
        self,
        target: LegalAddress,
        new_node: UKMutableNode,
        op: LegalOperation,
        selector: dict[str, Any],
    ) -> bool:
        schedule_node, _, _ = self._find_node_by_target(target)
        if schedule_node is None or _uk_kind_value(schedule_node.kind) != "schedule":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry replacement: target "
                    "did not resolve to a schedule carrier."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "schedule_target_unresolved",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        anchor = str(selector.get("anchor") or "")
        if not _compact_normalized_text(anchor):
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
                message="UK replay skipped schedule-list-entry replacement: selector had no entry anchor.",
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "invalid_selector",
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        entry_rows: list[tuple[int, UKMutableNode]] = [
            (idx, child)
            for idx, child in enumerate(schedule_node.children)
            if _uk_kind_value(child.kind) == "schedule_entry"
        ]
        anchor_norm = _compact_normalized_text(anchor)
        matches = [
            (idx, child)
            for idx, child in entry_rows
            if _compact_normalized_text(child.text) == anchor_norm
        ]
        match_mode = "exact"
        if not matches:
            matches = [
                (idx, child)
                for idx, child in entry_rows
                if _compact_normalized_text(child.text).startswith(anchor_norm)
            ]
            match_mode = "prefix"
        if not matches:
            article_anchor_norm = _compact_schedule_entry_anchor_without_article(anchor)
            matches = [
                (idx, child)
                for idx, child in entry_rows
                if article_anchor_norm
                and (
                    _compact_schedule_entry_anchor_without_article(child.text) == article_anchor_norm
                    or _compact_schedule_entry_anchor_without_article(child.text).startswith(article_anchor_norm)
                )
            ]
            match_mode = "article"
        if len(matches) != 1:
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_UNRESOLVED_RULE_ID,
                message=(
                    "UK replay skipped schedule-list-entry replacement: entry "
                    "anchor did not resolve uniquely."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "anchor": anchor,
                    "reason_code": "anchor_not_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            return False
        replace_idx = matches[0][0]
        if match_mode == "prefix":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_PREFIX_NORMALIZED_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry anchor as the "
                    "unique prefix of a longer source entry."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_prefix_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
        elif match_mode == "article":
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_ANCHOR_ARTICLE_NORMALIZED_RULE_ID,
                message=(
                    "UK replay resolved a schedule-list-entry anchor after "
                    "normalizing a leading article."
                ),
                op=op,
                detail={
                    "target": str(target),
                    "selector": dict(selector),
                    "reason_code": "anchor_leading_article_unique",
                    "anchor_match_count": len(matches),
                    "entry_count": len(entry_rows),
                    "family": "source_schedule_list_entry_elaboration",
                    "blocking": False,
                    "strict_disposition": "record",
                    "quirks_disposition": "record",
                },
            )
        for key in ("eId", "id"):
            new_node.attrs.pop(key, None)
        children = list(schedule_node.children)
        children[replace_idx] = new_node
        uk_replace_children(schedule_node, children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_SCHEDULE_LIST_ENTRY_REPLACE_RESOLVED_RULE_ID,
            message=(
                "UK replay applied a schedule-list-entry replacement after the "
                "source entry anchor resolved to exactly one direct schedule child."
            ),
            op=op,
            detail={
                "target": str(target),
                "selector": dict(selector),
                "reason_code": "explicit_entry_anchor_unique",
                "matched_index": replace_idx,
                "match_mode": match_mode,
                "entry_count": len(entry_rows),
                "family": "source_schedule_list_entry_elaboration",
                "blocking": False,
                "strict_disposition": "record",
                "quirks_disposition": "record",
            },
        )
        return True


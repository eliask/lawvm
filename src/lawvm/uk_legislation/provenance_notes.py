"""Structured provenance-note keys and decoders for UK replay ops."""
from __future__ import annotations

import json
from typing import Any

from lawvm.core.ir import LegalOperation


NOTE_FRAGMENT_SUB = "fragment_substitution:"
NOTE_EFFECT_TYPE = "uk_effect_type:"
NOTE_ORIGINAL_REF = "original_ref:"
NOTE_RAW_TEXT = "raw_text:"
NOTE_REWRITE_WITNESS = "rewrite_witness:"
NOTE_TEXT_REWRITE_RULE = "text_rewrite_rule:"
NOTE_PRECEDING_EID = "preceding_eid:"
NOTE_METADATA_SOURCE_FALLBACK = "metadata_source_fallback:"
NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR = "crossheading_group_repeal_selector:"
NOTE_TABLE_CELL_SELECTOR = "table_cell_selector:"
NOTE_TABLE_ROW_INSERT_SELECTOR = "table_row_insert_selector:"
NOTE_TABLE_COLUMN_INSERT_SELECTOR = "table_column_insert_selector:"
NOTE_SCHEDULE_LIST_ENTRY_SELECTOR = "schedule_list_entry_selector:"
NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR = "schedule_list_entry_table_rows_selector:"
NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR = "schedule_table_end_rows_selector:"
NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR = "schedule_list_entry_repeal_selector:"
NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR = "schedule_list_entry_replace_selector:"
NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION = "source_label_change_substitution:"


def _json_dict_note(op: LegalOperation, prefix: str) -> dict[str, Any] | None:
    for note in getattr(op, "provenance_tags", ()) or ():
        note_text = str(note)
        if not note_text.startswith(prefix):
            continue
        try:
            payload = json.loads(note_text[len(prefix) :])
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
    return None


def _table_cell_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK table-cell selector data carried on a lowered text op."""
    return _json_dict_note(op, NOTE_TABLE_CELL_SELECTOR)


def _crossheading_group_repeal_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK cross-heading group repeal selector data."""
    return _json_dict_note(op, NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR)


def _table_row_insert_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK table-row insertion selector data carried on a lowered insert op."""
    return _json_dict_note(op, NOTE_TABLE_ROW_INSERT_SELECTOR)


def _table_column_insert_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK table-column insertion selector data carried on a lowered insert op."""
    return _json_dict_note(op, NOTE_TABLE_COLUMN_INSERT_SELECTOR)


def _schedule_list_entry_table_rows_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK schedule-list table-row insertion selector data."""
    return _json_dict_note(op, NOTE_SCHEDULE_LIST_ENTRY_TABLE_ROWS_SELECTOR)


def _schedule_table_end_rows_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK schedule table end-row insertion selector data."""
    return _json_dict_note(op, NOTE_SCHEDULE_TABLE_END_ROWS_SELECTOR)


def _schedule_list_entry_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK schedule-list-entry selector data carried on a lowered insert op."""
    return _json_dict_note(op, NOTE_SCHEDULE_LIST_ENTRY_SELECTOR)


def _schedule_list_entry_repeal_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK schedule-list-entry selector data carried on a lowered repeal op."""
    return _json_dict_note(op, NOTE_SCHEDULE_LIST_ENTRY_REPEAL_SELECTOR)


def _schedule_list_entry_replace_selector(op: LegalOperation) -> dict[str, Any] | None:
    """Return UK schedule-list-entry selector data carried on a lowered replace op."""
    return _json_dict_note(op, NOTE_SCHEDULE_LIST_ENTRY_REPLACE_SELECTOR)

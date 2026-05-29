"""Typed table surface model for LawVM.

This module defines the canonical typed representation for table content that
downstream layers (semantic projection, diff, viewer) consume.  The IR layer
preserves table structure as ``IRNode(kind="table")`` subtrees; this model
projects those subtrees into a normalized, strongly-typed surface.

See ``notes/PRO_TABLE_IR.md`` for the architectural rationale.

API tier: stable kernel surface type.  New code should prefer these types
when working with table content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_ROW_KEY_BASES = frozenset({"explicit_label", "named_anchor", "header_key", "ordinal"})
_ROW_KEY_STRENGTHS = frozenset({"strong", "medium", "weak"})


@dataclass(frozen=True)
class RowKey:
    """Identity key for a table row.

    ``basis`` describes how the key was derived:
    - ``explicit_label``: row carries an explicit label/number in the source.
    - ``named_anchor``: row identified by a named entity (court name, tariff code).
    - ``header_key``: row matched against header column values.
    - ``ordinal``: positional index only (not durable across insert/delete churn).

    ``strength`` reflects confidence that this key is stable across amendments:
    - ``strong``: explicit label or named anchor in source.
    - ``medium``: header-derived or promoted from heuristic.
    - ``weak``: ordinal fallback only.
    """

    basis: Literal["explicit_label", "named_anchor", "header_key", "ordinal"]
    value: str
    strength: Literal["strong", "medium", "weak"]

    def __post_init__(self) -> None:
        if self.basis not in _ROW_KEY_BASES:
            raise ValueError(f"unsupported RowKey.basis: {self.basis!r}")
        if not self.value:
            raise ValueError("RowKey.value must be non-empty")
        if self.strength not in _ROW_KEY_STRENGTHS:
            raise ValueError(f"unsupported RowKey.strength: {self.strength!r}")


@dataclass(frozen=True)
class TableCell:
    """A single cell in a table row."""

    column_key: str
    text: str
    rowspan: int = 1
    colspan: int = 1

    def __post_init__(self) -> None:
        if not self.column_key:
            raise ValueError("TableCell.column_key must be non-empty")
        if self.rowspan < 1:
            raise ValueError("TableCell.rowspan must be >= 1")
        if self.colspan < 1:
            raise ValueError("TableCell.colspan must be >= 1")


@dataclass(frozen=True)
class TableRow:
    """A single row in a table, carrying its identity key and cell content."""

    row_key: RowKey
    cells: tuple[TableCell, ...] = ()
    source_basis: str = ""  # xml_table / named_row_promotion / etc.

    def __post_init__(self) -> None:
        if not isinstance(self.row_key, RowKey):
            raise ValueError("TableRow.row_key must be a RowKey")
        cells = tuple(self.cells)
        if not all(isinstance(cell, TableCell) for cell in cells):
            raise ValueError("TableRow.cells must contain TableCell records")
        object.__setattr__(self, "cells", cells)
        seen: set[str] = set()
        for cell in self.cells:
            if cell.column_key in seen:
                raise ValueError(f"TableRow cells must have unique column_key values, got {cell.column_key!r}")
            seen.add(cell.column_key)


@dataclass(frozen=True)
class TableBody:
    """Typed table surface projected from IR table subtrees.

    This is the canonical downstream representation of a table. It carries
    enough structure for table-aware diff, viewer rendering, and row-level
    targeting without requiring callers to traverse raw IR subtrees.
    """

    table_id: str
    caption: str = ""
    columns: tuple[str, ...] = ()
    rows: tuple[TableRow, ...] = ()

    def __post_init__(self) -> None:
        if not self.table_id:
            raise ValueError("TableBody.table_id must be non-empty")
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", tuple(self.rows))
        if any(not column for column in self.columns):
            raise ValueError("TableBody.columns must be non-empty strings")
        if not all(isinstance(column, str) for column in self.columns):
            raise ValueError("TableBody.columns must be strings")
        if not all(isinstance(row, TableRow) for row in self.rows):
            raise ValueError("TableBody.rows must contain TableRow records")


def table_body_to_flat_text(table: TableBody) -> str:
    """Serialize a TableBody to flat text, matching the comparison format.

    This produces the same space-joined-row output that the prior flattening
    code generated, so existing diff scores are preserved.
    """
    row_parts: list[str] = []
    for row in table.rows:
        cell_texts = [cell.text for cell in row.cells if cell.text]
        row_text = " ".join(cell_texts).strip()
        if row_text:
            row_parts.append(row_text)
    return " ".join(row_parts)

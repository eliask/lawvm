from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.table_model import RowKey, TableBody, TableCell, TableRow


def test_row_key_rejects_empty_value() -> None:
    with pytest.raises(ValueError, match="RowKey.value must be non-empty"):
        RowKey(basis="ordinal", value="", strength="weak")


def test_row_key_rejects_unknown_basis_and_strength() -> None:
    with pytest.raises(ValueError, match="unsupported RowKey.basis"):
        RowKey(basis=cast(Any, "row_number"), value="1", strength="weak")
    with pytest.raises(ValueError, match="unsupported RowKey.strength"):
        RowKey(basis="ordinal", value="1", strength=cast(Any, "maybe"))


def test_table_cell_rejects_empty_column_key() -> None:
    with pytest.raises(ValueError, match="TableCell.column_key must be non-empty"):
        TableCell(column_key="", text="x")


def test_table_cell_rejects_non_positive_spans() -> None:
    with pytest.raises(ValueError, match="rowspan must be >= 1"):
        TableCell(column_key="a", text="x", rowspan=0)
    with pytest.raises(ValueError, match="colspan must be >= 1"):
        TableCell(column_key="a", text="x", colspan=0)


def test_table_row_rejects_duplicate_column_keys() -> None:
    with pytest.raises(ValueError, match="unique column_key"):
        TableRow(
            row_key=RowKey(basis="ordinal", value="1", strength="weak"),
            cells=(
                TableCell(column_key="a", text="x"),
                TableCell(column_key="a", text="y"),
            ),
        )


def test_table_body_rejects_empty_identifier() -> None:
    with pytest.raises(ValueError, match="TableBody.table_id must be non-empty"):
        TableBody(table_id="")


def test_table_body_rejects_blank_columns() -> None:
    with pytest.raises(ValueError, match="columns must be non-empty strings"):
        TableBody(table_id="t1", columns=("a", ""))


def test_table_row_and_body_normalize_collections() -> None:
    cells = [TableCell(column_key="a", text="x")]
    rows = [TableRow(row_key=RowKey(basis="ordinal", value="1", strength="weak"), cells=cast(Any, cells))]

    body = TableBody(table_id="t1", columns=cast(Any, ["a"]), rows=cast(Any, rows))
    cells.append(TableCell(column_key="b", text="y"))
    rows.append(TableRow(row_key=RowKey(basis="ordinal", value="2", strength="weak")))

    assert body.rows == (rows[0],)
    assert body.rows[0].cells == (TableCell(column_key="a", text="x"),)
    assert body.columns == ("a",)


def test_table_row_and_body_reject_malformed_lanes() -> None:
    with pytest.raises(ValueError, match="row_key must be a RowKey"):
        TableRow(row_key=cast(Any, "row-1"))
    with pytest.raises(ValueError, match="cells must contain TableCell"):
        TableRow(
            row_key=RowKey(basis="ordinal", value="1", strength="weak"),
            cells=cast(Any, ("bad",)),
        )
    with pytest.raises(ValueError, match="rows must contain TableRow"):
        TableBody(table_id="t1", rows=cast(Any, ("bad",)))

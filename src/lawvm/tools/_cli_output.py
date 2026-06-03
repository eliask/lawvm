"""Shared output-format dispatcher for LawVM CLI query commands.

Provides:
  - format_table(): render rows as an aligned text table.
  - json_safe():    coerce DuckDB result values to JSON-safe types.
  - emit_rows():    dispatch across table|json|jsonl|csv|parquet output formats.

Extracted from refs_query, actors_query, pools_query, fi_proposals_query to
avoid duplication (AGENTS.md §1.9).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------


def json_safe(v: Any) -> Any:
    """Coerce a DuckDB result value to a JSON-serializable type."""
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, list):
        return v
    return str(v)


# ---------------------------------------------------------------------------
# Table formatter
# ---------------------------------------------------------------------------


def format_table(columns: List[str], rows: List[tuple], max_col_width: int = 50) -> str:
    """Format query results as an aligned text table.

    Args:
        columns:       Column names.
        rows:          Query result rows (tuples).
        max_col_width: Maximum characters per cell before truncation.

    Returns:
        Formatted string (may be multi-line).
    """
    if not rows:
        return "(0 rows)"

    str_rows = [[str(v) if v is not None else "" for v in row] for row in rows]

    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], min(len(val), max_col_width))

    header = "  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))
    separator = "  ".join("-" * w for w in widths)

    lines = [header, separator]
    for row in str_rows:
        line = "  ".join(
            val[:max_col_width].ljust(w)
            for val, w in zip(row, widths, strict=True)
        )
        lines.append(line)

    lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output dispatcher
# ---------------------------------------------------------------------------


def emit_rows(
    *,
    columns: List[str],
    rows: List[tuple],
    output_format: str,
    data_dir: str,
    result_stem: str,
    duckdb_query: Optional[str] = None,
    duckdb_con: Any = None,
) -> None:
    """Dispatch query results to the requested output format.

    Args:
        columns:       Column names from the DuckDB result description.
        rows:          Fetched rows.
        output_format: One of table|json|jsonl|csv|parquet.
        data_dir:      Used for parquet output path.
        result_stem:   File stem for parquet output (e.g. "_refs_query_result").
        duckdb_query:  Original SQL query; required for parquet output mode.
        duckdb_con:    Open DuckDB connection; required for parquet output mode.
    """
    if output_format == "json":
        out = []
        for row in rows:
            out.append(dict(zip(columns, [json_safe(v) for v in row], strict=True)))
        print(json.dumps(out, indent=2, ensure_ascii=False))

    elif output_format == "jsonl":
        for row in rows:
            obj = dict(zip(columns, [json_safe(v) for v in row], strict=True))
            print(json.dumps(obj, ensure_ascii=False))

    elif output_format == "csv":
        import csv as csv_mod
        import io
        buf = io.StringIO()
        writer = csv_mod.writer(buf)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)
        print(buf.getvalue(), end="")

    elif output_format == "parquet":
        if duckdb_con is None or duckdb_query is None:
            print(
                "error: parquet output requires a live DuckDB connection and query",
                file=sys.stderr,
            )
            sys.exit(1)
        out_path = Path(data_dir) / f"{result_stem}.parquet"
        duckdb_con.execute(f"COPY ({duckdb_query}) TO '{out_path}' (FORMAT PARQUET)")
        print(f"Written {len(rows)} rows to {out_path}")

    else:
        # Default: table
        print(format_table(columns, rows))

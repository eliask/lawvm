"""lawvm sql — ad-hoc SQL queries over LawVM canonical projections.

Uses DuckDB as the local analytics backend to query JSONL/Parquet row
projections produced by ``lawvm export-projections``.

Usage:
    lawvm sql --query "SELECT statute_id, score FROM statutes ORDER BY score LIMIT 10"
    lawvm sql --query "SELECT statute_id, count(*) n FROM sections WHERE diff_kind != 'identical' GROUP BY 1 ORDER BY 2 DESC LIMIT 20"
    lawvm sql                            # show available tables
    lawvm sql --data-dir .tmp/my_proj    # custom projection directory
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional


def _check_duckdb() -> bool:
    """Check if duckdb is importable."""
    try:
        import duckdb  # noqa: F401  # ty: ignore[unresolved-import]
        return True
    except ImportError:
        return False


def _discover_tables(data_dir: Path) -> dict[str, Path]:
    """Discover available projection files (Parquet preferred, JSONL fallback).

    Returns dict of table_name -> file_path.
    """
    tables: dict[str, Path] = {}

    for parquet in sorted(data_dir.glob("*.parquet")):
        name = parquet.stem
        tables[name] = parquet

    for jsonl in sorted(data_dir.glob("*.jsonl")):
        name = jsonl.stem
        if name not in tables:  # Parquet takes priority
            tables[name] = jsonl

    return tables


def _register_tables(con: Any, tables: dict[str, Path]) -> list[str]:
    """Register discovered files as DuckDB views/tables.

    Returns list of registered table names.
    """
    registered = []
    for name, path in sorted(tables.items()):
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')"
            )
        elif suffix == ".jsonl":
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_json_auto('{path}')"
            )
        else:
            continue
        registered.append(name)
    return registered


def _print_table_info(con: Any, table_names: list[str]) -> None:
    """Print schema summary for each registered table."""
    for name in table_names:
        print(f"\n  {name}:")
        try:
            result = con.execute(f"DESCRIBE {name}").fetchall()
            for col_name, col_type, *_ in result:
                print(f"    {col_name:30s} {col_type}")
            row_count = con.execute(f"SELECT count(*) FROM {name}").fetchone()
            if row_count:
                print(f"    ({row_count[0]:,} rows)")
        except Exception as exc:
            print(f"    (error describing: {exc})")


def _format_results(columns: list[str], rows: list[tuple], max_width: int = 120) -> str:
    """Format query results as an aligned text table."""
    if not rows:
        return "(0 rows)"

    # Convert all values to strings
    str_rows = [[str(v) for v in row] for row in rows]

    # Compute column widths
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], min(len(val), 60))

    # Build header
    header = "  ".join(c.ljust(w) for c, w in zip(columns, widths))
    separator = "  ".join("-" * w for w in widths)

    # Build rows
    lines = [header, separator]
    for row in str_rows:
        line = "  ".join(
            val[:60].ljust(w) for val, w in zip(row, widths)
        )
        lines.append(line)

    lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


def run_sql(
    *,
    query: Optional[str] = None,
    data_dir: str = ".tmp/projections",
    output_format: str = "table",
) -> None:
    """Run a SQL query against LawVM projections or show available tables."""
    if not _check_duckdb():
        print(
            "error: duckdb is not installed.\n\n"
            "Install it with:\n"
            "  uv pip install duckdb\n\n"
            "Or add duckdb to your project dependencies:\n"
            '  duckdb = ">=1.0"\n\n'
            "DuckDB is the local analytics backend for LawVM SQL queries.\n"
            "See notes/PRO_QUERY_TOOLING.md for the architecture vision.",
            file=sys.stderr,
        )
        sys.exit(1)

    import duckdb  # ty: ignore[unresolved-import]

    dd = Path(data_dir)
    tables = _discover_tables(dd)

    if not tables and query:
        # Allow pure SQL even without projections (e.g. "SELECT 1+1")
        con = duckdb.connect(":memory:")
        try:
            result = con.execute(query)
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            print(_format_results(columns, rows))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        finally:
            con.close()
        return

    if not tables and not query:
        print(
            f"No projection files found in {dd}/\n\n"
            "Run 'lawvm export-projections' first to generate JSONL/Parquet files.\n"
            "Or pass --data-dir to point to a different directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Connect and register tables
    con = duckdb.connect(":memory:")
    try:
        registered = _register_tables(con, tables)

        if not query:
            # Show available tables
            print(f"Available tables in {dd}/:")
            _print_table_info(con, registered)
            print(
                "\nRun queries with:\n"
                '  lawvm sql --query "SELECT * FROM statutes LIMIT 10"'
            )
            return

        # Execute the query
        try:
            result = con.execute(query)
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()

            if output_format == "json":
                import json
                out = []
                for row in rows:
                    out.append(dict(zip(columns, [_json_safe(v) for v in row])))
                print(json.dumps(out, indent=2, ensure_ascii=False))
            elif output_format == "csv":
                import csv as csv_mod
                import io
                buf = io.StringIO()
                writer = csv_mod.writer(buf)
                writer.writerow(columns)
                for row in rows:
                    writer.writerow(row)
                print(buf.getvalue(), end="")
            else:
                print(_format_results(columns, rows))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    finally:
        con.close()


def _json_safe(v: Any) -> Any:
    """Convert duckdb types to JSON-serializable Python types."""
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return str(v)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args: Any) -> None:
    run_sql(
        query=getattr(args, "query", None),
        data_dir=getattr(args, "data_dir", ".tmp/projections"),
        output_format=getattr(args, "output_format", "table"),
    )

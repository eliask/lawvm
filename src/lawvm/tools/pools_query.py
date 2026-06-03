"""lawvm pools -- query PoolMention records from fi_pools.parquet.

Queries the ``fi_pools.parquet`` projection produced by ``lawvm export-projections``.
Without a query, shows the schema.  With filters, returns matching pool mentions.

Usage:
    lawvm pools                                    # show schema
    lawvm pools --statute 711/2022                 # all pools in statute
    lawvm pools --quantity-kind budget_line        # budget-line mentions only
    lawvm pools --unit 'g Cd/ha/5 v'               # capacity caps by unit
    lawvm pools --as-of 2023-01-01                 # mentions valid at date
    lawvm pools --confidence exact                 # registry-resolved mentions

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (currently only 'fi' supported; default 'fi')
  --as-of DATE      filter to mentions valid at DATE
  -o {table|json|jsonl|csv|parquet}  output format (default: table)
  --limit N         limit rows
  --data-dir PATH   override default data directory
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Default data-dir
# ---------------------------------------------------------------------------


def _default_data_dir() -> str:
    return ".tmp/projections"


def _fi_pools_path(data_dir: str) -> Path:
    """Return the path to fi_pools.parquet in data_dir."""
    return Path(data_dir) / "fi_pools.parquet"


def _fi_pools_jsonl_path(data_dir: str) -> Path:
    return Path(data_dir) / "fi_pools.jsonl"


# ---------------------------------------------------------------------------
# DuckDB query runner
# ---------------------------------------------------------------------------


def _check_duckdb() -> bool:
    try:
        import duckdb  # noqa: F401  # ty: ignore[unresolved-import]
        return True
    except ImportError:
        return False


def _find_pools_source(data_dir: str) -> Optional[Path]:
    """Return path to fi_pools.parquet or fi_pools.jsonl, preferring Parquet."""
    p = _fi_pools_path(data_dir)
    if p.exists():
        return p
    j = _fi_pools_jsonl_path(data_dir)
    if j.exists():
        return j
    return None


def _build_query(
    pools_source: str,
    *,
    statute: Optional[str] = None,
    provision: Optional[str] = None,
    quantity_kind: Optional[str] = None,
    confidence: Optional[str] = None,
    unit: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for fi_pools with applied filters."""
    suffix = Path(pools_source).suffix.lower()
    if suffix == ".parquet":
        source_expr = f"read_parquet('{pools_source}')"
    else:
        source_expr = f"read_json_auto('{pools_source}')"

    # Columns to select
    cols = [
        "source_statute_id",
        "source_provision_ref_str",
        "pool_canonical_id",
        "quantity_phrase",
        "quantity_kind",
        "resolution_confidence",
        "numeric_value",
        "unit",
        "valid_at_start",
        "valid_at_end",
    ]
    select_cols = ", ".join(cols)

    # Build WHERE clauses
    conditions: List[str] = []

    if statute:
        conditions.append(f"source_statute_id = '{statute}'")

    if provision:
        conditions.append(
            f"(source_provision_ref_str = '{provision}' OR "
            f"source_provision_ref_str LIKE '{provision}%')"
        )

    if quantity_kind:
        # Normalize hyphen to underscore (CLI: budget-line, DB: budget_line)
        normalized = quantity_kind.replace("-", "_")
        conditions.append(f"quantity_kind = '{normalized}'")

    if confidence:
        normalized_conf = confidence.replace("-", "_")
        conditions.append(f"resolution_confidence = '{normalized_conf}'")

    if unit:
        safe_unit = unit.replace("'", "''")
        conditions.append(f"unit = '{safe_unit}'")

    if as_of:
        conditions.append(
            f"(valid_at_start IS NULL OR valid_at_start <= '{as_of}')"
        )
        conditions.append(
            f"(valid_at_end IS NULL OR valid_at_end >= '{as_of}')"
        )

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    limit_clause = f" LIMIT {limit}" if limit else ""

    return (
        f"SELECT {select_cols} FROM {source_expr}"
        f"{where_clause}"
        f" ORDER BY source_statute_id, source_provision_ref_str, quantity_kind"
        f"{limit_clause}"
    )


def _format_table(columns: List[str], rows: List[tuple]) -> str:
    """Format query results as an aligned text table."""
    if not rows:
        return "(0 rows)"

    str_rows = [[str(v) if v is not None else "" for v in row] for row in rows]

    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], min(len(val), 50))

    header = "  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))
    separator = "  ".join("-" * w for w in widths)

    lines = [header, separator]
    for row in str_rows:
        line = "  ".join(
            val[:50].ljust(w) for val, w in zip(row, widths, strict=True)
        )
        lines.append(line)

    lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


def _json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


# ---------------------------------------------------------------------------
# Schema printer
# ---------------------------------------------------------------------------

_POOLS_SCHEMA = [
    ("source_statute_id", "VARCHAR", "Source statute ID, e.g. '711/2022'"),
    ("source_provision_ref_str", "VARCHAR", "Source provision ref, e.g. '711/2022/3'"),
    ("pool_canonical_id", "VARCHAR", "Registry canonical ID (null if UNRESOLVED)"),
    ("quantity_phrase", "VARCHAR", "Literal phrase from source text"),
    ("quantity_kind", "VARCHAR", "budget_line|fiscal_pool|capacity_cap|threshold|formula_term|unresolved"),
    ("resolution_confidence", "VARCHAR", "exact|approximate|unresolved"),
    ("numeric_value", "DOUBLE", "Extracted numeric value (null if not applicable)"),
    ("unit", "VARCHAR", "Extracted unit string, e.g. 'g/ha/v', 'EUR' (null if not applicable)"),
    ("source_span_file", "VARCHAR", "Source XML file path (null if not available)"),
    ("source_span_byte_offset", "INTEGER", "Byte offset in source file (null if not available)"),
    ("source_span_byte_len", "INTEGER", "Span length in bytes (null if not available)"),
    ("valid_at_start", "DATE", "When this mention state begins (null = always)"),
    ("valid_at_end", "DATE", "When it ends (null = currently valid)"),
]


def _print_schema() -> None:
    print("\n  fi_pools:")
    for col, typ, desc in _POOLS_SCHEMA:
        print(f"    {col:35s} {typ:12s} {desc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_pools(
    *,
    statute: Optional[str] = None,
    provision: Optional[str] = None,
    quantity_kind: Optional[str] = None,
    confidence: Optional[str] = None,
    unit: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
    data_dir: str = ".tmp/projections",
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the pools query and print results."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm pools' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    pools_path = _find_pools_source(data_dir)

    if pools_path is None:
        print(
            f"No fi_pools.parquet or fi_pools.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections --include-pools' first to generate projection files.\n"
            "Or pass --data-dir to point to a different directory.",
            file=sys.stderr,
        )
        _print_schema()
        sys.exit(1)

    if not _check_duckdb():
        print(
            "error: duckdb is not installed.\n\n"
            "Install it with:\n"
            "  uv pip install duckdb\n",
            file=sys.stderr,
        )
        sys.exit(1)

    import duckdb  # ty: ignore[unresolved-import]

    # If no filters specified, just show schema + row count
    has_filters = any([statute, provision, quantity_kind, confidence, unit, as_of])
    if not has_filters and not limit:
        _print_schema()
        con = duckdb.connect(":memory:")
        suffix = pools_path.suffix.lower()
        if suffix == ".parquet":
            src = f"read_parquet('{pools_path}')"
        else:
            src = f"read_json_auto('{pools_path}')"
        row_count = con.execute(f"SELECT count(*) FROM {src}").fetchone()
        con.close()
        if row_count:
            print(f"\n  ({row_count[0]:,} rows in fi_pools)")
        print("\nFilter with: --statute STATUTE, --quantity-kind KIND, --confidence CONF, ...")
        return

    query = _build_query(
        str(pools_path),
        statute=statute,
        provision=provision,
        quantity_kind=quantity_kind,
        confidence=confidence,
        unit=unit,
        as_of=as_of,
        limit=limit,
    )

    con = duckdb.connect(":memory:")
    try:
        result = con.execute(query)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        if output_format == "json":
            out = []
            for row in rows:
                out.append(dict(zip(columns, [_json_safe(v) for v in row], strict=True)))
            print(json.dumps(out, indent=2, ensure_ascii=False))
        elif output_format == "jsonl":
            for row in rows:
                obj = dict(zip(columns, [_json_safe(v) for v in row], strict=True))
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
            out_path = Path(data_dir) / "_pools_query_result.parquet"
            con.execute(
                f"COPY ({query}) TO '{out_path}' (FORMAT PARQUET)"
            )
            print(f"Written {len(rows)} rows to {out_path}")
        else:
            print(_format_table(columns, rows))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# CLI entry point (called from cli.py)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    run_pools(
        statute=getattr(args, "statute", None),
        provision=getattr(args, "provision", None),
        quantity_kind=getattr(args, "quantity_kind", None),
        confidence=getattr(args, "confidence", None),
        unit=getattr(args, "unit", None),
        as_of=getattr(args, "as_of", None),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", ".tmp/projections"),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

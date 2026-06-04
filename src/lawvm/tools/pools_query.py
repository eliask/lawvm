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

import sys
from pathlib import Path
from typing import Any, List, Optional

from lawvm.tools._cli_duckdb import (
    as_of_conditions,
    find_source_file,
    require_duckdb,
    source_expr_for_path,
)
from lawvm.tools._cli_output import emit_rows, format_table, json_safe


# ---------------------------------------------------------------------------
# Default data-dir
# ---------------------------------------------------------------------------

_DEFAULT_DATA_DIR = "data/fi/v1"


def _find_pools_source(data_dir: str) -> Optional[Path]:
    """Return path to fi_pools.parquet or fi_pools.jsonl, preferring Parquet."""
    return find_source_file(data_dir, "fi_pools")


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
    source_expr = source_expr_for_path(Path(pools_source))

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
        conditions.extend(as_of_conditions(as_of))

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    limit_clause = f" LIMIT {limit}" if limit else ""

    return (
        f"SELECT {select_cols} FROM {source_expr}"
        f"{where_clause}"
        f" ORDER BY source_statute_id, source_provision_ref_str, quantity_kind"
        f"{limit_clause}"
    )


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
    data_dir: str = _DEFAULT_DATA_DIR,
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

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

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

    duckdb = require_duckdb()

    # If no filters specified, just show schema + row count
    has_filters = any([statute, provision, quantity_kind, confidence, unit, as_of])
    if not has_filters and not limit:
        _print_schema()
        con = duckdb.connect(":memory:")
        src = source_expr_for_path(pools_path)
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
        emit_rows(
            columns=columns,
            rows=rows,
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_pools_query_result",
            duckdb_query=query,
            duckdb_con=con,
        )
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
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

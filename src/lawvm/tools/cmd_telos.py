"""lawvm telos — query telos/purpose sections from sections.parquet.

Convenience command over the is_purpose_section flag added by feature #5.
Returns purpose sections for a statute (or all statutes) with their
purpose_text_snippet.

Usage:
    lawvm telos --statute 711/2022
    lawvm telos --statute 711/2022 --as-of 2024-01-01
    lawvm telos -o json

Backed by sections.parquet (is_purpose_section=true rows).

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (fi only)
  --as-of DATE      (note: sections projection is current-state; as-of is best-effort)
  -o {table|json|jsonl|csv|parquet}
  --limit N
  --data-dir PATH
"""
from __future__ import annotations

import sys
from typing import Any, List, Optional

from lawvm.tools._cli_duckdb import (
    find_source_file,
    require_duckdb,
    source_expr_for_path,
)
from lawvm.tools._cli_output import emit_rows


_DEFAULT_DATA_DIR = "data/fi/v1"


def _build_telos_query(
    *,
    sections_expr: str,
    statute: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for telos/purpose sections."""
    conditions: List[str] = ["is_purpose_section = true"]
    if statute:
        safe = statute.replace("'", "''")
        conditions.append(f"statute_id = '{safe}'")

    where = " AND ".join(conditions)
    limit_clause = f" LIMIT {limit}" if limit else ""

    return (
        f"SELECT statute_id, section_key, "
        f"LEFT(purpose_text_snippet, 300) AS purpose_text_snippet "
        f"FROM {sections_expr} "
        f"WHERE {where} "
        f"ORDER BY statute_id, section_key "
        f"{limit_clause}"
    )


def run_telos(
    *,
    statute: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the telos command."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm telos' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

    sections_path = find_source_file(data_dir, "sections")
    if sections_path is None:
        print(
            f"No sections.parquet or sections.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections' first to generate sections projection.\n"
            "Or pass --data-dir to point to the projection directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    duckdb = require_duckdb()
    sections_expr = source_expr_for_path(sections_path)

    query = _build_telos_query(
        sections_expr=sections_expr,
        statute=statute,
        limit=limit,
    )

    con = duckdb.connect(":memory:")
    try:
        # Gracefully handle projections without telos columns (#5 may not have landed)
        # Check if is_purpose_section column exists
        check_query = f"SELECT * FROM {sections_expr} LIMIT 0"
        check_res = con.execute(check_query)
        col_names = [d[0] for d in check_res.description]
        if "is_purpose_section" not in col_names:
            print(
                "Warning: 'is_purpose_section' column not found in sections projection.\n"
                "Telos-section flag (feature #5) may not have been applied to this export.\n"
                "Run 'lawvm export-projections' after updating to a version with telos support.",
                file=sys.stderr,
            )
            sys.exit(1)

        result = con.execute(query)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        if not rows:
            target = f"statute {statute!r}" if statute else "any statute"
            print(
                f"(0 rows) — no telos/purpose sections found for {target}.\n"
                "Check --statute value or --data-dir.",
                file=sys.stderr,
            )

        emit_rows(
            columns=columns,
            rows=rows,
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_telos_query_result",
            duckdb_query=query,
            duckdb_con=con,
        )
    finally:
        con.close()


def main(args: Any) -> None:
    run_telos(
        statute=getattr(args, "statute", None),
        as_of=getattr(args, "as_of", None),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

"""lawvm pit-timeline — provision history at section level (Parquet-backed).

Shows the amendment history for a given provision by querying the ops
projection (which tracks compiled operation events per amendment).

The name 'pit-timeline' disambiguates from the existing 'lawvm timeline'
command (which does live replay-based PIT materialization for a statute).
This command is the index-backed query surface: it reads ops.parquet for
amendment history and sections.parquet for current text state.

Usage:
    lawvm pit-timeline --provision 2002/738
    lawvm pit-timeline --provision 2002/738/7
    lawvm pit-timeline --provision 2002/738 --since 2015-01-01
    lawvm pit-timeline --provision 2002/738 --until 2023-12-31
    lawvm pit-timeline --provision 2002/738 --include-amendments -o json

Backed by:
  - ops.parquet        — compiled operation events per amendment per provision
  - sections.parquet   — current section text (for --include-text)
  - fi_refs.parquet    — reference edges valid_at intervals (for ref context)

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (fi only)
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

_TIMELINE_COLUMNS = [
    "amendment_id",
    "op_type",
    "target_kind",
    "target_section",
    "target_chapter",
    "target_paragraph",
    "statute_id",
]


def _build_timeline_query(
    *,
    ops_expr: str,
    provision_ref: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for ops history of a provision.

    The ops projection has: statute_id, amendment_id, op_type, target_kind,
    target_section, target_chapter, target_paragraph.

    The 'provision_ref' may be:
      - YYYY/NNN         → filter on statute_id
      - YYYY/NNN/section → filter on statute_id + target_section
      - chapter:N/section:M notation → filter on target_kind/target_section
    """
    safe_ref = provision_ref.replace("'", "''")
    conditions: List[str] = []

    # Parse provision_ref into statute + section component
    parts = safe_ref.split("/")
    if len(parts) >= 2:
        # Statute ID is first two slash-separated components: YYYY/NNN
        statute_id = "/".join(parts[:2])
        conditions.append(f"statute_id = '{statute_id}'")
        if len(parts) >= 3:
            # Third component is section
            section_part = parts[2]
            conditions.append(f"target_section = '{section_part}'")
    else:
        # Could be an address-style ref like 'section:4'
        conditions.append(
            f"(statute_id = '{safe_ref}' OR "
            f"target_section LIKE '%{safe_ref}%')"
        )

    # Date filters applied to amendment_id if it is in YYYY/NNN form;
    # since/until are best-effort filters on amendment year prefix.
    if since:
        since_year = since[:4]
        conditions.append(
            f"CAST(SPLIT_PART(amendment_id, '/', 1) AS INTEGER) >= {since_year}"
        )
    if until:
        until_year = until[:4]
        conditions.append(
            f"CAST(SPLIT_PART(amendment_id, '/', 1) AS INTEGER) <= {until_year}"
        )

    where = " AND ".join(conditions)
    limit_clause = f" LIMIT {limit}" if limit else ""

    return (
        f"SELECT amendment_id, op_type, target_kind, "
        f"target_section, target_chapter, target_paragraph, statute_id "
        f"FROM {ops_expr} "
        f"WHERE {where} "
        f"ORDER BY amendment_id "
        f"{limit_clause}"
    )


def run_pit_timeline(
    *,
    provision: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    include_amendments: bool = False,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the pit-timeline command."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm pit-timeline' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

    ops_path = find_source_file(data_dir, "ops")
    if ops_path is None:
        print(
            f"No ops.parquet or ops.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections' first to generate ops projection.\n"
            "Or pass --data-dir to point to the projection directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    duckdb = require_duckdb()
    ops_expr = source_expr_for_path(ops_path)

    query = _build_timeline_query(
        ops_expr=ops_expr,
        provision_ref=provision,
        since=since,
        until=until,
        limit=limit,
    )

    con = duckdb.connect(":memory:")
    try:
        result = con.execute(query)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        if not rows:
            print(
                f"(0 rows) — no amendment history found for provision {provision!r}.\n"
                "Check --provision format (e.g. '2002/738' or '2002/738/7'), "
                "--since/--until range, or --data-dir.",
                file=sys.stderr,
            )

        emit_rows(
            columns=columns,
            rows=rows,
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_pit_timeline_result",
            duckdb_query=query,
            duckdb_con=con,
        )
    finally:
        con.close()


def main(args: Any) -> None:
    run_pit_timeline(
        provision=args.provision,
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        include_amendments=getattr(args, "include_amendments", False),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

"""lawvm pit-diff — provision text + structural diff between two PIT states.

Shows what changed in a provision (or statute) between two point-in-time dates,
based on the amendment ops that fall within the date window.

The name 'pit-diff' disambiguates from the existing 'lawvm diff' command
(which does per-provision replay-vs-oracle similarity scoring).

Usage:
    lawvm pit-diff --provision 2002/738 --t1 2020-01-01 --t2 2024-01-01
    lawvm pit-diff --provision 2002/738/7 --t1 2019-01-01 --t2 2023-01-01
    lawvm pit-diff --provision 2002/738 --t1 2020-01-01 --t2 2024-01-01 --include-text
    lawvm pit-diff --provision 2002/738 --t1 2020-01-01 --t2 2024-01-01 --include-refs

Output:
    Amendment operations (from ops.parquet) that happened between t1 and t2,
    optionally with current section text (from sections.parquet) and reference
    state diff (from fi_refs.parquet valid_at intervals).

Backed by:
  - ops.parquet       — amendment operations indexed by statute+provision
  - sections.parquet  — current section text (for --include-text)
  - fi_refs.parquet   — reference validity intervals (for --include-refs)

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


def _build_ops_diff_query(
    *,
    ops_expr: str,
    provision_ref: str,
    t1: str,
    t2: str,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for ops between t1 and t2 affecting the provision.

    The ops projection has: statute_id, amendment_id, op_type, target_kind,
    target_section, target_chapter, target_paragraph.
    amendment_id is in YYYY/NNN form; we filter by year prefix between t1 and t2.
    """
    safe_ref = provision_ref.replace("'", "''")
    conditions: List[str] = []

    parts = safe_ref.split("/")
    if len(parts) >= 2:
        statute_id = "/".join(parts[:2])
        conditions.append(f"statute_id = '{statute_id}'")
        if len(parts) >= 3:
            section_part = parts[2]
            conditions.append(f"target_section = '{section_part}'")
    else:
        conditions.append(f"statute_id = '{safe_ref}'")

    t1_year = t1[:4]
    t2_year = t2[:4]
    conditions.append(
        f"CAST(SPLIT_PART(amendment_id, '/', 1) AS INTEGER) >= {t1_year}"
    )
    conditions.append(
        f"CAST(SPLIT_PART(amendment_id, '/', 1) AS INTEGER) <= {t2_year}"
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


def _build_refs_diff_query(
    *,
    refs_expr: str,
    provision_ref: str,
    t1: str,
    t2: str,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for refs that changed validity between t1 and t2.

    Shows refs that started or ended in the [t1, t2] window (i.e., newly
    established or newly broken/ended refs during the diff window).
    """
    safe_ref = provision_ref.replace("'", "''")
    parts = safe_ref.split("/")
    if len(parts) >= 2:
        statute_id = "/".join(parts[:2])
        src_cond = f"source_statute_id = '{statute_id}'"
    else:
        src_cond = f"source_statute_id = '{safe_ref}'"

    window_cond = (
        f"("
        f"(valid_at_start IS NOT NULL AND valid_at_start BETWEEN '{t1}' AND '{t2}') OR "
        f"(valid_at_end IS NOT NULL AND valid_at_end BETWEEN '{t1}' AND '{t2}')"
        f")"
    )
    where = f"{src_cond} AND {window_cond}"
    limit_clause = f" LIMIT {limit}" if limit else ""

    return (
        f"SELECT 'refs_change' AS change_kind, "
        f"source_provision_ref_str, target_provision_ref_str, "
        f"cite_kind, cite_confidence, valid_at_start, valid_at_end "
        f"FROM {refs_expr} "
        f"WHERE {where} "
        f"ORDER BY valid_at_start, source_provision_ref_str "
        f"{limit_clause}"
    )


def run_pit_diff(
    *,
    provision: str,
    t1: str,
    t2: str,
    include_text: bool = False,
    include_refs: bool = False,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the pit-diff command."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm pit-diff' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

    ops_path = find_source_file(data_dir, "ops")
    if ops_path is None:
        print(
            f"No ops.parquet or ops.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections' first.\n"
            "Or pass --data-dir to point to the projection directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    duckdb = require_duckdb()
    ops_expr = source_expr_for_path(ops_path)

    query = _build_ops_diff_query(
        ops_expr=ops_expr,
        provision_ref=provision,
        t1=t1,
        t2=t2,
        limit=limit,
    )

    con = duckdb.connect(":memory:")
    try:
        result = con.execute(query)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        if not rows:
            print(
                f"(0 rows) — no amendment ops found for {provision!r} between "
                f"{t1} and {t2}.\n"
                "Check --provision, --t1/--t2 date range, or --data-dir.",
                file=sys.stderr,
            )

        emit_rows(
            columns=columns,
            rows=rows,
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_pit_diff_ops_result",
            duckdb_query=query,
            duckdb_con=con,
        )

        # Optional: include section text (current state from sections.parquet)
        if include_text:
            sections_path = find_source_file(data_dir, "sections")
            if sections_path is None:
                print(
                    f"  Warning: sections.parquet not found in {data_dir}/; "
                    "skipping --include-text.",
                    file=sys.stderr,
                )
            else:
                sec_expr = source_expr_for_path(sections_path)
                safe_ref = provision.replace("'", "''")
                parts = safe_ref.split("/")
                if len(parts) >= 2:
                    statute_id = "/".join(parts[:2])
                    sec_where = f"statute_id = '{statute_id}'"
                    if len(parts) >= 3:
                        sec_where += f" AND section_key LIKE '%{parts[2]}%'"
                else:
                    sec_where = f"statute_id = '{safe_ref}'"
                sec_query = (
                    f"SELECT statute_id, section_key, LEFT(replay_text, 400) AS replay_text "
                    f"FROM {sec_expr} WHERE {sec_where} ORDER BY section_key"
                )
                sec_res = con.execute(sec_query)
                sec_cols = [d[0] for d in sec_res.description]
                sec_rows = sec_res.fetchall()
                if sec_rows:
                    print("\n-- Current section text --")
                    emit_rows(
                        columns=sec_cols,
                        rows=sec_rows,
                        output_format=output_format,
                        data_dir=data_dir,
                        result_stem="_pit_diff_sections_result",
                        duckdb_query=sec_query,
                        duckdb_con=con,
                    )

        # Optional: include refs diff
        if include_refs:
            refs_path = find_source_file(data_dir, "fi_refs")
            if refs_path is None:
                print(
                    f"  Warning: fi_refs.parquet not found in {data_dir}/; "
                    "skipping --include-refs.",
                    file=sys.stderr,
                )
            else:
                refs_expr = source_expr_for_path(refs_path)
                refs_query = _build_refs_diff_query(
                    refs_expr=refs_expr,
                    provision_ref=provision,
                    t1=t1,
                    t2=t2,
                    limit=limit,
                )
                refs_res = con.execute(refs_query)
                refs_cols = [d[0] for d in refs_res.description]
                refs_rows = refs_res.fetchall()
                if refs_rows:
                    print("\n-- Reference changes --")
                    emit_rows(
                        columns=refs_cols,
                        rows=refs_rows,
                        output_format=output_format,
                        data_dir=data_dir,
                        result_stem="_pit_diff_refs_result",
                        duckdb_query=refs_query,
                        duckdb_con=con,
                    )
                elif refs_path:
                    print(
                        f"  (0 reference changes for {provision!r} between {t1} and {t2})",
                        file=sys.stderr,
                    )
    finally:
        con.close()


def main(args: Any) -> None:
    run_pit_diff(
        provision=args.provision,
        t1=args.t1,
        t2=args.t2,
        include_text=getattr(args, "include_text", False),
        include_refs=getattr(args, "include_refs", False),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

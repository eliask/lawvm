"""lawvm preparatory-refs — query PreparatoryReference records.

Queries the ``fi_preparatory_refs.parquet`` projection produced by
``lawvm export-projections --include-preparatory-refs`` or
``lawvm rebuild-indexes``.

Without a query, shows the schema.  With filters, returns matching rows.

Usage:
    lawvm preparatory-refs                               # show schema
    lawvm preparatory-refs --statute 711/2022           # refs FROM this statute
    lawvm preparatory-refs --kind he                    # HE rows only
    lawvm preparatory-refs --committee HaVM             # by committee abbrev
    lawvm preparatory-refs --he-year 2021               # HEs from 2021
    lawvm preparatory-refs --he-number 173              # specific HE number
    lawvm preparatory-refs --eu-celex 32017R2226        # by CELEX
    lawvm preparatory-refs --kind eu_regulation -o json # JSON output

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (currently only 'fi' supported; default 'fi')
  --as-of DATE      filter to references valid at DATE
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
    check_duckdb,
    find_source_file,
    require_duckdb,
    source_expr_for_path,
)
from lawvm.tools._cli_output import emit_rows, format_table, json_safe


# ---------------------------------------------------------------------------
# Default data-dir
# ---------------------------------------------------------------------------


_DEFAULT_DATA_DIR = ".tmp/projections"


def _find_preparatory_refs_source(data_dir: str) -> Optional[Path]:
    """Return path to fi_preparatory_refs.parquet or .jsonl, preferring Parquet."""
    return find_source_file(data_dir, "fi_preparatory_refs")


def _build_query(
    refs_source: str,
    *,
    statute: Optional[str] = None,
    kind: Optional[str] = None,
    committee: Optional[str] = None,
    he_year: Optional[int] = None,
    he_number: Optional[int] = None,
    eu_celex: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for fi_preparatory_refs with applied filters."""
    source_expr = source_expr_for_path(Path(refs_source))

    # All columns
    cols = [
        "source_statute_id",
        "kind",
        "canonical_id",
        "raw_text",
        "committee_abbrev",
        "he_year",
        "he_number",
        "eu_form",
        "eu_number",
        "eu_year",
        "celex",
        "oj_series",
        "oj_number",
        "oj_date",
        "oj_page",
        "confidence",
        "valid_at_start",
        "valid_at_end",
    ]
    select_cols = ", ".join(cols)

    # Build WHERE clauses
    conditions: List[str] = []

    if statute:
        conditions.append(f"source_statute_id = '{statute}'")

    if kind:
        # Normalize hyphen to underscore for CLI usability
        normalized = kind.replace("-", "_")
        conditions.append(f"kind = '{normalized}'")

    if committee:
        # Match committee_abbrev case-insensitively
        conditions.append(
            f"upper(committee_abbrev) = upper('{committee}')"
        )

    if he_year is not None:
        conditions.append(f"he_year = {he_year}")

    if he_number is not None:
        conditions.append(f"he_number = {he_number}")

    if eu_celex:
        conditions.append(f"celex = '{eu_celex}'")

    if as_of:
        conditions.extend(as_of_conditions(as_of))

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    limit_clause = f" LIMIT {limit}" if limit else ""

    return (
        f"SELECT {select_cols} FROM {source_expr}"
        f"{where_clause}"
        f" ORDER BY source_statute_id, kind, canonical_id"
        f"{limit_clause}"
    )


# ---------------------------------------------------------------------------
# Schema printer
# ---------------------------------------------------------------------------

_PREP_REFS_SCHEMA = [
    ("source_statute_id",      "VARCHAR",  "Source statute ID, e.g. '711/2022'"),
    ("kind",                   "VARCHAR",  "he|committee_report|committee_opinion|parliament_response|parliament_response_comm|law_initiative|eu_regulation|eu_directive|eu_decision|oj_reference|unresolved"),
    ("canonical_id",           "VARCHAR",  "Canonical ID for joining (null if unresolved)"),
    ("raw_text",               "VARCHAR",  "Literal text span from source"),
    ("committee_abbrev",       "VARCHAR",  "Committee abbreviation, e.g. 'HaVM' (null if not committee)"),
    ("he_year",                "INTEGER",  "HE year (null if not HE)"),
    ("he_number",              "INTEGER",  "HE number (null if not HE)"),
    ("eu_form",                "VARCHAR",  "EU form string: EU|EY|EEY|ETY (null if not EU)"),
    ("eu_number",              "INTEGER",  "EU act number (null if not EU)"),
    ("eu_year",                "INTEGER",  "EU act year (null if not EU)"),
    ("celex",                  "VARCHAR",  "CELEX identifier, e.g. '32017R2226' (null if not present)"),
    ("oj_series",              "VARCHAR",  "OJ series: L|C|S (null if not OJ)"),
    ("oj_number",              "INTEGER",  "OJ issue number (null if not OJ)"),
    ("oj_date",                "VARCHAR",  "OJ publication date ISO (null if not OJ)"),
    ("oj_page",                "INTEGER",  "OJ starting page (null if not OJ)"),
    ("confidence",             "VARCHAR",  "exact|approximate|unresolved"),
    ("valid_at_start",         "DATE",     "When this reference state begins (null = always)"),
    ("valid_at_end",           "DATE",     "When it ends (null = currently valid)"),
]


def _print_schema() -> None:
    print("\n  fi_preparatory_refs:")
    for col, typ, desc in _PREP_REFS_SCHEMA:
        print(f"    {col:30s} {typ:12s} {desc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_preparatory_refs(
    *,
    statute: Optional[str] = None,
    kind: Optional[str] = None,
    committee: Optional[str] = None,
    he_year: Optional[int] = None,
    he_number: Optional[int] = None,
    eu_celex: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
    data_dir: str = ".tmp/projections",
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the preparatory-refs query and print results."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm preparatory-refs' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    refs_path = _find_preparatory_refs_source(data_dir)

    if refs_path is None:
        print(
            f"No fi_preparatory_refs.parquet or fi_preparatory_refs.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections --include-preparatory-refs' or "
            "'lawvm rebuild-indexes' first.\n"
            "Or pass --data-dir to point to a different directory.",
            file=sys.stderr,
        )
        _print_schema()
        sys.exit(1)

    duckdb = require_duckdb()

    # If no filters specified, just show schema + row count
    has_filters = any([statute, kind, committee, he_year is not None,
                       he_number is not None, eu_celex, as_of])
    if not has_filters and not limit:
        _print_schema()
        con = duckdb.connect(":memory:")
        src = source_expr_for_path(refs_path)
        row_count = con.execute(f"SELECT count(*) FROM {src}").fetchone()
        con.close()
        if row_count:
            print(f"\n  ({row_count[0]:,} rows in fi_preparatory_refs)")
        print(
            "\nFilter with: --statute STATUTE, --kind KIND, --committee ABBREV, "
            "--he-year N, --he-number N, --eu-celex CELEX, ..."
        )
        return

    query = _build_query(
        str(refs_path),
        statute=statute,
        kind=kind,
        committee=committee,
        he_year=he_year,
        he_number=he_number,
        eu_celex=eu_celex,
        as_of=as_of,
        limit=limit,
    )

    con = duckdb.connect(":memory:")
    result = con.execute(query)
    columns = [desc[0] for desc in result.description]
    rows = result.fetchall()
    emit_rows(
        columns=columns,
        rows=rows,
        output_format=output_format,
        data_dir=data_dir,
        result_stem="_preparatory_refs_query_result",
        duckdb_query=query,
        duckdb_con=con,
    )
    con.close()


# ---------------------------------------------------------------------------
# CLI entry point (called from cli.py)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    run_preparatory_refs(
        statute=getattr(args, "statute", None),
        kind=getattr(args, "kind", None),
        committee=getattr(args, "committee", None),
        he_year=getattr(args, "he_year", None),
        he_number=getattr(args, "he_number", None),
        eu_celex=getattr(args, "eu_celex", None),
        as_of=getattr(args, "as_of", None),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", ".tmp/projections"),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

"""lawvm inline-citations — query InlineCitation records.

Queries the ``fi_inline_citations.parquet`` projection produced by
``lawvm export-projections --include-inline-citations`` or
``lawvm rebuild-indexes``.

Without a query, shows the schema. With filters, returns matching rows.

Usage:
    lawvm inline-citations                               # show schema
    lawvm inline-citations --source-doc-id 711/2022     # citations FROM this statute
    lawvm inline-citations --kind court_kko             # KKO court decisions only
    lawvm inline-citations --kind he_inline             # HE->HE citations in HE prose
    lawvm inline-citations --context he_rationale       # perustelut citations only
    lawvm inline-citations --case-year 2018             # citations from year 2018
    lawvm inline-citations --source-doc-id 116/2024 -o json  # JSON output

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (currently only 'fi' supported; default 'fi')
  --as-of DATE      (not applicable to inline citations — no temporal interval)
  -o {table|json|jsonl|csv|parquet}  output format (default: table)
  --limit N         limit rows
  --data-dir PATH   override default data directory
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List, Optional

from lawvm.tools._cli_duckdb import (
    find_source_file,
    require_duckdb,
    source_expr_for_path,
)
from lawvm.tools._cli_output import emit_rows


# ---------------------------------------------------------------------------
# Default data-dir
# ---------------------------------------------------------------------------


_DEFAULT_DATA_DIR = "data/fi/v1"


def _find_inline_citations_source(data_dir: str) -> Optional[Path]:
    """Return path to fi_inline_citations.parquet or .jsonl, preferring Parquet."""
    return find_source_file(data_dir, "fi_inline_citations")


def _build_query(
    citations_source: str,
    *,
    source_doc_id: Optional[str] = None,
    source_doc_kind: Optional[str] = None,
    kind: Optional[str] = None,
    context: Optional[str] = None,
    case_year: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for fi_inline_citations with applied filters."""
    source_expr = source_expr_for_path(Path(citations_source))

    # All columns
    cols = [
        "source_doc_id",
        "source_doc_kind",
        "source_provision_ref",
        "kind",
        "canonical_id",
        "raw_text",
        "case_year",
        "case_number",
        "context",
        "source_span_file",
    ]
    select_cols = ", ".join(cols)

    # Build WHERE clauses
    conditions: List[str] = []

    if source_doc_id:
        conditions.append(f"source_doc_id = '{source_doc_id}'")

    if source_doc_kind:
        normalized = source_doc_kind.replace("-", "_")
        conditions.append(f"source_doc_kind = '{normalized}'")

    if kind:
        normalized = kind.replace("-", "_")
        conditions.append(f"kind = '{normalized}'")

    if context:
        normalized = context.replace("-", "_")
        conditions.append(f"context = '{normalized}'")

    if case_year is not None:
        conditions.append(f"case_year = {case_year}")

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    limit_clause = f" LIMIT {limit}" if limit else ""

    return (
        f"SELECT {select_cols} FROM {source_expr}"
        f"{where_clause}"
        f" ORDER BY source_doc_id, kind, canonical_id"
        f"{limit_clause}"
    )


# ---------------------------------------------------------------------------
# Schema printer
# ---------------------------------------------------------------------------

_INLINE_CITATIONS_SCHEMA = [
    ("source_doc_id",         "VARCHAR",   "Source document ID: statute ID (e.g. '711/2022') or HE ID (e.g. '116/2024')"),
    ("source_doc_kind",       "VARCHAR",   "statute | he"),
    ("source_provision_ref",  "VARCHAR",   "Provision context from ancestor eId (empty if unavailable)"),
    ("kind",                  "VARCHAR",   "court_kko|court_kho|ombudsman_eoa|chancellor_oka|statute_inline|he_inline|vtv_report|working_group_memo|parliament_kirjelma|old_committee|unresolved"),
    ("canonical_id",          "VARCHAR",   "Canonical ID for joining (null for old_committee/unresolved)"),
    ("raw_text",              "VARCHAR",   "Literal citation text from source (verbatim)"),
    ("case_year",             "INTEGER",   "Year component for court/eoa/oka/vtv/he/ek citations (null for statute_inline)"),
    ("case_number",           "INTEGER",   "Number component for court/eoa/oka/vtv/he/ek citations (null for statute_inline)"),
    ("context",               "VARCHAR",   "enacted_statute_body|he_rationale|he_introduction|preliminary_work|other"),
    ("source_span_file",      "VARCHAR",   "Source file path or farchive locator (null for in-memory)"),
    ("source_span_byte_offset","INTEGER",  "Byte offset in source file (null)"),
    ("source_span_byte_len",  "INTEGER",   "Byte length of span (null)"),
]


def _print_schema() -> None:
    print("\n  fi_inline_citations:")
    for col, typ, desc in _INLINE_CITATIONS_SCHEMA:
        print(f"    {col:30s} {typ:12s} {desc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_inline_citations(
    *,
    source_doc_id: Optional[str] = None,
    source_doc_kind: Optional[str] = None,
    kind: Optional[str] = None,
    context: Optional[str] = None,
    case_year: Optional[int] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the inline-citations query and print results."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm inline-citations' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

    citations_path = _find_inline_citations_source(data_dir)

    if citations_path is None:
        print(
            f"No fi_inline_citations.parquet or fi_inline_citations.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections --include-inline-citations' or "
            "'lawvm rebuild-indexes' first.\n"
            "Or pass --data-dir to point to a different directory.",
            file=sys.stderr,
        )
        _print_schema()
        sys.exit(1)

    duckdb = require_duckdb()

    # If no filters specified, just show schema + row count
    has_filters = any([
        source_doc_id, source_doc_kind, kind, context,
        case_year is not None,
    ])
    if not has_filters and not limit:
        _print_schema()
        con = duckdb.connect(":memory:")
        src = source_expr_for_path(citations_path)
        row_count = con.execute(f"SELECT count(*) FROM {src}").fetchone()
        con.close()
        if row_count:
            print(f"\n  ({row_count[0]:,} rows in fi_inline_citations)")
        print(
            "\nFilter with: --source-doc-id ID, --kind KIND, --context CTX, "
            "--case-year N, --source-doc-kind statute|he, ..."
        )
        return

    query = _build_query(
        str(citations_path),
        source_doc_id=source_doc_id,
        source_doc_kind=source_doc_kind,
        kind=kind,
        context=context,
        case_year=case_year,
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
        result_stem="_inline_citations_query_result",
        duckdb_query=query,
        duckdb_con=con,
    )
    con.close()


# ---------------------------------------------------------------------------
# CLI entry point (called from cli.py)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    run_inline_citations(
        source_doc_id=getattr(args, "source_doc_id", None),
        source_doc_kind=getattr(args, "source_doc_kind", None),
        kind=getattr(args, "kind", None),
        context=getattr(args, "context", None),
        case_year=getattr(args, "case_year", None),
        as_of=getattr(args, "as_of", None),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", ".tmp/projections"),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

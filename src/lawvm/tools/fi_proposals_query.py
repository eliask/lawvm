"""lawvm fi-proposals -- query fi_he_corpus.parquet.

See module docstring for full usage.
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
from lawvm.tools._cli_output import emit_rows, format_table, json_safe

_DEFAULT_DATA_DIR = "data/fi/v1"


def _find_corpus_source(data_dir: str) -> Optional[Path]:
    return find_source_file(data_dir, "fi_he_corpus")


def _build_proposals_query(
    source: str,
    *,
    ministry: Optional[str] = None,
    year: Optional[int] = None,
    year_range: Optional[str] = None,
    lifecycle: Optional[str] = None,
    structured_only: bool = False,
    pdf_only: bool = False,
    limit: Optional[int] = None,
) -> str:
    source_expr = source_expr_for_path(Path(source))

    cols = [
        "he_id", "he_year", "he_number", "he_uri", "lang",
        "ministry_canonical_id", "ministry_show_as", "title",
        "date_issued", "structural_tier", "is_structured",
        "finlex_state", "source_zip_sha256", "ingest_timestamp",
    ]
    select_cols = ", ".join(cols)
    conditions: List[str] = []

    if ministry:
        safe = ministry.replace("'", "''")
        conditions.append(
            f"(lower(ministry_show_as) LIKE lower('%{safe}%') OR "
            f"lower(ministry_canonical_id) LIKE lower('%{safe}%'))"
        )
    if year is not None:
        conditions.append(f"he_year = {year}")
    if year_range:
        parts = year_range.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            conditions.append(f"he_year BETWEEN {parts[0]} AND {parts[1]}")
    if lifecycle:
        safe = lifecycle.replace("'", "''")
        conditions.append(f"finlex_state = '{safe}'")
    if structured_only:
        conditions.append("is_structured = true")
    if pdf_only:
        conditions.append("is_structured = false")

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    limit_clause = f" LIMIT {limit}" if limit else ""
    return (
        f"SELECT {select_cols} FROM {source_expr}"
        f"{where_clause}"
        f" ORDER BY he_year DESC, he_number DESC"
        f"{limit_clause}"
    )


_HE_CORPUS_SCHEMA = [
    ("he_id", "VARCHAR", "HE identifier, e.g. 'HE 98/1996 vp'"),
    ("he_year", "INTEGER", "Calendar year"),
    ("he_number", "INTEGER", "HE number within year"),
    ("he_uri", "VARCHAR", "FRBR work URI"),
    ("lang", "VARCHAR", "fin | swe"),
    ("languages", "LIST<VARCHAR>", "All language codes for this HE"),
    ("ministry_canonical_id", "VARCHAR", "e.g. 'fi.ministry-of-justice'"),
    ("ministry_show_as", "VARCHAR", "e.g. 'Oikeusministeriö'"),
    ("title", "VARCHAR", "Document title"),
    ("date_issued", "VARCHAR", "ISO date"),
    ("structural_tier", "VARCHAR", "full_akn | pdf_wrapper"),
    ("is_structured", "BOOLEAN", "True for FULL_AKN, False for PDF_WRAPPER"),
    ("finlex_state", "VARCHAR", "e.g. 'closed'"),
    ("source_zip_sha256", "VARCHAR", "SHA256 of source zip"),
    ("ingest_timestamp", "VARCHAR", "ISO ingest timestamp"),
]


def _print_schema() -> None:
    print("\n  fi_he_corpus:")
    for col, typ, desc in _HE_CORPUS_SCHEMA:
        print(f"    {col:35s} {typ:20s} {desc}")


def run_fi_proposals(
    *,
    ministry: Optional[str] = None,
    year: Optional[int] = None,
    year_range: Optional[str] = None,
    lifecycle: Optional[str] = None,
    structured_only: bool = False,
    pdf_only: bool = False,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    if jurisdiction != "fi":
        print(f"error: 'lawvm fi-proposals' only supports 'fi'; got {jurisdiction!r}", file=sys.stderr)
        sys.exit(1)

    corpus_path = _find_corpus_source(data_dir)
    if corpus_path is None:
        print(
            f"No fi_he_corpus.parquet or fi_he_corpus.jsonl found in {data_dir}/\n"
            "Run 'lawvm sync-fi-proposals' first.",
            file=sys.stderr,
        )
        _print_schema()
        sys.exit(1)

    duckdb = require_duckdb()

    has_filters = any([ministry, year is not None, year_range, lifecycle, structured_only, pdf_only])
    if not has_filters and not limit:
        _print_schema()
        con = duckdb.connect(":memory:")
        src = source_expr_for_path(corpus_path)
        row_count = con.execute(f"SELECT count(*) FROM {src}").fetchone()
        con.close()
        if row_count:
            print(f"\n  ({row_count[0]:,} rows in fi_he_corpus)")
        print("\nFilter with: --ministry TEXT, --year N, --year-range Y1:Y2, --lifecycle STATE, ...")
        return

    query = _build_proposals_query(
        str(corpus_path),
        ministry=ministry, year=year, year_range=year_range,
        lifecycle=lifecycle, structured_only=structured_only,
        pdf_only=pdf_only, limit=limit,
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
            result_stem="_fi_proposals_query_result",
            duckdb_query=query,
            duckdb_con=con,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        con.close()


def main(args: Any) -> None:
    year_raw = getattr(args, "year", None)
    year: Optional[int] = int(year_raw) if year_raw is not None else None
    run_fi_proposals(
        ministry=getattr(args, "ministry", None),
        year=year,
        year_range=getattr(args, "year_range", None),
        lifecycle=getattr(args, "lifecycle", None),
        structured_only=getattr(args, "structured_only", False),
        pdf_only=getattr(args, "pdf_only", False),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

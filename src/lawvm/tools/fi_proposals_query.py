"""lawvm fi-proposals -- query fi_he_corpus.parquet.

See module docstring for full usage.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, List, Optional


def _default_data_dir() -> str:
    return "data/fi/v1"


def _fi_he_corpus_path(data_dir: str) -> Path:
    return Path(data_dir) / "fi_he_corpus.parquet"


def _fi_he_corpus_jsonl_path(data_dir: str) -> Path:
    return Path(data_dir) / "fi_he_corpus.jsonl"


def _check_duckdb() -> bool:
    try:
        import duckdb  # noqa: F401
        return True
    except ImportError:
        return False


def _find_corpus_source(data_dir: str) -> Optional[Path]:
    p = _fi_he_corpus_path(data_dir)
    if p.exists():
        return p
    j = _fi_he_corpus_jsonl_path(data_dir)
    if j.exists():
        return j
    return None


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
    suffix = Path(source).suffix.lower()
    if suffix == ".parquet":
        source_expr = f"read_parquet('{source}')"
    else:
        source_expr = f"read_json_auto('{source}')"

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


def _format_table(columns: List[str], rows: List[tuple]) -> str:
    if not rows:
        return "(0 rows)"
    str_rows = [[str(v) if v is not None else "" for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], min(len(val), 60))
    header = "  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True))
    separator = "  ".join("-" * w for w in widths)
    lines = [header, separator]
    for row in str_rows:
        lines.append("  ".join(
            val[:60].ljust(w) for val, w in zip(row, widths, strict=True)
        ))
    lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


def _json_safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool, list)):
        return v
    return str(v)


def run_fi_proposals(
    *,
    ministry: Optional[str] = None,
    year: Optional[int] = None,
    year_range: Optional[str] = None,
    lifecycle: Optional[str] = None,
    structured_only: bool = False,
    pdf_only: bool = False,
    limit: Optional[int] = None,
    data_dir: str = "data/fi/v1",
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

    if not _check_duckdb():
        print("error: duckdb is not installed. Install with: uv pip install duckdb", file=sys.stderr)
        sys.exit(1)

    import duckdb

    has_filters = any([ministry, year is not None, year_range, lifecycle, structured_only, pdf_only])
    if not has_filters and not limit:
        _print_schema()
        con = duckdb.connect(":memory:")
        suffix = corpus_path.suffix.lower()
        src = f"read_parquet('{corpus_path}')" if suffix == ".parquet" else f"read_json_auto('{corpus_path}')"
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

        if output_format == "json":
            out = [dict(zip(columns, [_json_safe(v) for v in row], strict=True)) for row in rows]
            print(json.dumps(out, indent=2, ensure_ascii=False))
        elif output_format == "jsonl":
            for row in rows:
                print(json.dumps(dict(zip(columns, [_json_safe(v) for v in row], strict=True)), ensure_ascii=False))
        elif output_format == "csv":
            import csv as csv_mod, io
            buf = io.StringIO()
            writer = csv_mod.writer(buf)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(row)
            print(buf.getvalue(), end="")
        elif output_format == "parquet":
            out_path = Path(data_dir) / "_fi_proposals_query_result.parquet"
            con.execute(f"COPY ({query}) TO '{out_path}' (FORMAT PARQUET)")
            print(f"Written {len(rows)} rows to {out_path}")
        else:
            print(_format_table(columns, rows))
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
        data_dir=getattr(args, "data_dir", "data/fi/v1"),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

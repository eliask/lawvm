"""lawvm refs — query ReferenceMention records from fi_refs.parquet.

Queries the ``fi_refs.parquet`` projection produced by ``lawvm export-projections``.
Without a query, shows the schema.  With filters, returns matching citation edges.

Usage:
    lawvm refs                                # show schema
    lawvm refs --from 711/2022               # all citations FROM lannoitelaki
    lawvm refs --to 711/2022/7               # all citations TO lannoitelaki §7
    lawvm refs --confidence broken           # broken references
    lawvm refs --cite-kind eu                # EU citations only
    lawvm refs --from 711/2022 -o json       # JSON output

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (currently only 'fi' supported; default 'fi')
  --as-of DATE      filter to references valid at DATE
  -o {table|json|jsonl|csv|parquet}  output format (default: table)
  --limit N         limit rows
  --data-dir PATH   override default data directory
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Default data-dir
# ---------------------------------------------------------------------------


def _default_data_dir() -> str:
    return ".tmp/projections"


def _fi_refs_path(data_dir: str) -> Path:
    """Return the path to fi_refs.parquet in data_dir."""
    return Path(data_dir) / "fi_refs.parquet"


def _fi_refs_jsonl_path(data_dir: str) -> Path:
    return Path(data_dir) / "fi_refs.jsonl"


# ---------------------------------------------------------------------------
# DuckDB query runner
# ---------------------------------------------------------------------------


def _check_duckdb() -> bool:
    try:
        import duckdb  # noqa: F401  # ty: ignore[unresolved-import]
        return True
    except ImportError:
        return False


def _find_refs_source(data_dir: str) -> Optional[Path]:
    """Return path to fi_refs.parquet or fi_refs.jsonl, preferring Parquet."""
    p = _fi_refs_path(data_dir)
    if p.exists():
        return p
    j = _fi_refs_jsonl_path(data_dir)
    if j.exists():
        return j
    return None


def _build_query(
    refs_source: str,
    *,
    from_ref: Optional[str] = None,
    to_ref: Optional[str] = None,
    cite_kind: Optional[str] = None,
    confidence: Optional[str] = None,
    broken_after: Optional[str] = None,
    broken_before: Optional[str] = None,
    as_of: Optional[str] = None,
    include_source_span: bool = False,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for fi_refs with applied filters."""
    suffix = Path(refs_source).suffix.lower()
    if suffix == ".parquet":
        source_expr = f"read_parquet('{refs_source}')"
    else:
        source_expr = f"read_json_auto('{refs_source}')"

    # Columns to select
    cols = [
        "source_statute_id",
        "source_provision_ref_str",
        "target_statute_id",
        "target_provision_ref_str",
        "cite_kind",
        "cite_confidence",
        "edge_subtype",
        "phrase_lemma",
        "valid_at_start",
        "valid_at_end",
        "target_stat_hash",
    ]
    if include_source_span:
        cols += ["source_span_file", "source_span_byte_offset", "source_span_len"]

    select_cols = ", ".join(cols)

    # Build WHERE clauses
    conditions: List[str] = []

    if from_ref:
        # --from can be a statute_id or a provision_ref_str
        if "/" in from_ref.replace("/", "", 1):
            # Has section component — match provision_ref_str prefix
            conditions.append(
                f"(source_statute_id = '{from_ref}' OR "
                f"source_provision_ref_str LIKE '{from_ref}%')"
            )
        else:
            conditions.append(f"source_statute_id = '{from_ref}'")

    if to_ref:
        if "/" in to_ref.replace("/", "", 1):
            conditions.append(
                f"(target_statute_id = '{to_ref}' OR "
                f"target_provision_ref_str LIKE '{to_ref}%')"
            )
        else:
            conditions.append(f"target_statute_id = '{to_ref}'")

    if cite_kind:
        # Normalize hyphen to underscore (CLI uses cross-statute, DB stores cross_statute)
        normalized = cite_kind.replace("-", "_")
        conditions.append(f"cite_kind = '{normalized}'")

    if confidence:
        conditions.append(f"cite_confidence = '{confidence}'")

    if broken_after:
        # References that became BROKEN after this date
        conditions.append(f"cite_confidence = 'broken'")
        conditions.append(f"valid_at_end >= '{broken_after}'")

    if broken_before:
        conditions.append(f"cite_confidence = 'broken'")
        conditions.append(f"valid_at_end <= '{broken_before}'")

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
        f" ORDER BY source_statute_id, source_provision_ref_str"
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

_REFS_SCHEMA = [
    ("source_statute_id", "VARCHAR", "Source statute ID, e.g. '711/2022'"),
    ("source_provision_ref_str", "VARCHAR", "Source provision ref, e.g. '711/2022/7'"),
    ("target_statute_id", "VARCHAR", "Target statute ID (null if UNRESOLVED)"),
    ("target_provision_ref_str", "VARCHAR", "Target provision ref (null if UNRESOLVED)"),
    ("cite_kind", "VARCHAR", "internal|cross_statute|eu|treaty|non_statutory_instrument"),
    ("cite_confidence", "VARCHAR", "exact|approximate|ambiguous|unresolved|broken"),
    ("edge_subtype", "VARCHAR", "CITES|REPEALS|ISSUED_UNDER|ISSUES (null for in-prose)"),
    ("phrase_lemma", "VARCHAR", "ref_element|REPEALS|ISSUED_UNDER|ISSUES|eu_text_pattern"),
    ("source_span_file", "VARCHAR", "Source XML file path (null for metadata edges)"),
    ("source_span_byte_offset", "INTEGER", "Byte offset in source file (null for metadata)"),
    ("source_span_len", "INTEGER", "Span length in bytes (null for metadata)"),
    ("valid_at_start", "DATE", "When this reference state begins (null = always)"),
    ("valid_at_end", "DATE", "When it ends (null = currently valid)"),
    ("target_stat_hash", "VARCHAR", "SHA256[:16] of target at projection time"),
]


def _print_schema() -> None:
    print("\n  fi_refs:")
    for col, typ, desc in _REFS_SCHEMA:
        print(f"    {col:35s} {typ:12s} {desc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_refs(
    *,
    from_ref: Optional[str] = None,
    to_ref: Optional[str] = None,
    cite_kind: Optional[str] = None,
    confidence: Optional[str] = None,
    broken_after: Optional[str] = None,
    broken_before: Optional[str] = None,
    as_of: Optional[str] = None,
    include_source_span: bool = False,
    limit: Optional[int] = None,
    data_dir: str = ".tmp/projections",
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the refs query and print results."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm refs' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    refs_path = _find_refs_source(data_dir)

    if refs_path is None:
        print(
            f"No fi_refs.parquet or fi_refs.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections' first to generate projection files.\n"
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
    has_filters = any([from_ref, to_ref, cite_kind, confidence,
                       broken_after, broken_before, as_of])
    if not has_filters and not limit:
        # Show schema + row count
        _print_schema()
        con = duckdb.connect(":memory:")
        suffix = refs_path.suffix.lower()
        if suffix == ".parquet":
            src = f"read_parquet('{refs_path}')"
        else:
            src = f"read_json_auto('{refs_path}')"
        row_count = con.execute(f"SELECT count(*) FROM {src}").fetchone()
        con.close()
        if row_count:
            print(f"\n  ({row_count[0]:,} rows in fi_refs)")
        print("\nFilter with: --from STATUTE, --to STATUTE, --confidence CONF, ...")
        return

    query = _build_query(
        str(refs_path),
        from_ref=from_ref,
        to_ref=to_ref,
        cite_kind=cite_kind,
        confidence=confidence,
        broken_after=broken_after,
        broken_before=broken_before,
        as_of=as_of,
        include_source_span=include_source_span,
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
            out_path = Path(data_dir) / "_refs_query_result.parquet"
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
    run_refs(
        from_ref=getattr(args, "from_ref", None),
        to_ref=getattr(args, "to_ref", None),
        cite_kind=getattr(args, "cite_kind", None),
        confidence=getattr(args, "confidence", None),
        broken_after=getattr(args, "broken_after", None),
        broken_before=getattr(args, "broken_before", None),
        as_of=getattr(args, "as_of", None),
        include_source_span=getattr(args, "include_source_span", False),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", ".tmp/projections"),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

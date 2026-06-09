"""lawvm actors -- query ActorMention records from fi_actors.parquet.

Queries the ``fi_actors.parquet`` projection produced by ``lawvm export-projections``.
Without a query, shows the schema.  With filters, returns matching actor mentions.

Usage:
    lawvm actors                                  # show schema
    lawvm actors --statute 711/2022               # all actors in lannoitelaki
    lawvm actors --modal-kind duty                # duty-obligation mentions
    lawvm actors --confidence exact               # TLCOrganization-backed
    lawvm actors --role-pattern 'Ruoka.*'         # pattern match on canonical ID
    lawvm actors --as-of 2023-01-01               # mentions valid at date

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
from lawvm.tools._cli_output import emit_rows


# ---------------------------------------------------------------------------
# Default data-dir
# ---------------------------------------------------------------------------

_DEFAULT_DATA_DIR = "data/fi/v1"


def _find_actors_source(data_dir: str) -> Optional[Path]:
    """Return path to fi_actors.parquet or fi_actors.jsonl, preferring Parquet."""
    return find_source_file(data_dir, "fi_actors")


def _build_query(
    actors_source: str,
    *,
    statute: Optional[str] = None,
    provision: Optional[str] = None,
    modal_kind: Optional[str] = None,
    confidence: Optional[str] = None,
    role_pattern: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Build DuckDB SQL for fi_actors with applied filters."""
    source_expr = source_expr_for_path(Path(actors_source))

    # Columns to select
    cols = [
        "source_statute_id",
        "source_provision_ref_str",
        "actor_canonical_id",
        "actor_canonical_show_as",
        "actor_phrase",
        "modal_kind",
        "resolution_confidence",
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

    if modal_kind:
        # Normalize hyphen to underscore (CLI: passive-obligation, DB: passive_obligation)
        normalized = modal_kind.replace("-", "_")
        conditions.append(f"modal_kind = '{normalized}'")

    if confidence:
        normalized_conf = confidence.replace("-", "_")
        conditions.append(f"resolution_confidence = '{normalized_conf}'")

    if role_pattern:
        # SQL LIKE pattern: user passes 'Ruoka.*' -> convert to '%Ruoka%' or use SIMILAR TO
        # Use SIMILAR TO for regex-style patterns (DuckDB supports it)
        # Escape single quotes in pattern
        safe_pattern = role_pattern.replace("'", "''")
        conditions.append(
            f"(actor_canonical_id SIMILAR TO '{safe_pattern}' OR "
            f"actor_canonical_show_as SIMILAR TO '{safe_pattern}')"
        )

    if as_of:
        conditions.extend(as_of_conditions(as_of))

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    limit_clause = f" LIMIT {limit}" if limit else ""

    return (
        f"SELECT {select_cols} FROM {source_expr}"
        f"{where_clause}"
        f" ORDER BY source_statute_id, source_provision_ref_str, actor_canonical_id"
        f"{limit_clause}"
    )


# ---------------------------------------------------------------------------
# Schema printer
# ---------------------------------------------------------------------------

_ACTORS_SCHEMA = [
    ("source_statute_id", "VARCHAR", "Source statute ID, e.g. '711/2022'"),
    ("source_provision_ref_str", "VARCHAR", "Source provision ref, e.g. '711/2022/7'"),
    ("actor_canonical_id", "VARCHAR", "Registry canonical ID (null if UNRESOLVED)"),
    ("actor_canonical_show_as", "VARCHAR", "Canonical display string (null if UNRESOLVED)"),
    ("actor_phrase", "VARCHAR", "Literal phrase from source text"),
    ("modal_kind", "VARCHAR", "duty|discretion|permission|prohibition|mention|passive_obligation|unresolved"),
    ("resolution_confidence", "VARCHAR", "exact|registry_resolved|lifecycle_resolved|unresolved"),
    ("source_span_file", "VARCHAR", "Source XML file path (null if not available)"),
    ("source_span_byte_offset", "INTEGER", "Byte offset in source file (null if not available)"),
    ("source_span_byte_len", "INTEGER", "Span length in bytes (null if not available)"),
    ("valid_at_start", "DATE", "When this mention state begins (null = always)"),
    ("valid_at_end", "DATE", "When it ends (null = currently valid)"),
]


def _print_schema() -> None:
    print("\n  fi_actors:")
    for col, typ, desc in _ACTORS_SCHEMA:
        print(f"    {col:35s} {typ:12s} {desc}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_actors(
    *,
    statute: Optional[str] = None,
    provision: Optional[str] = None,
    modal_kind: Optional[str] = None,
    confidence: Optional[str] = None,
    role_pattern: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the actors query and print results."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm actors' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

    actors_path = _find_actors_source(data_dir)

    if actors_path is None:
        print(
            f"No fi_actors.parquet or fi_actors.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections --include-actors' first to generate projection files.\n"
            "Or pass --data-dir to point to a different directory.",
            file=sys.stderr,
        )
        _print_schema()
        sys.exit(1)

    duckdb = require_duckdb()

    # If no filters specified, just show schema + row count
    has_filters = any([statute, provision, modal_kind, confidence, role_pattern, as_of])
    if not has_filters and not limit:
        _print_schema()
        con = duckdb.connect(":memory:")
        src = source_expr_for_path(actors_path)
        row_count = con.execute(f"SELECT count(*) FROM {src}").fetchone()
        con.close()
        if row_count:
            print(f"\n  ({row_count[0]:,} rows in fi_actors)")
        print("\nFilter with: --statute STATUTE, --modal-kind KIND, --confidence CONF, ...")
        return

    query = _build_query(
        str(actors_path),
        statute=statute,
        provision=provision,
        modal_kind=modal_kind,
        confidence=confidence,
        role_pattern=role_pattern,
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
            result_stem="_actors_query_result",
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
    run_actors(
        statute=getattr(args, "statute", None),
        provision=getattr(args, "provision", None),
        modal_kind=getattr(args, "modal_kind", None),
        confidence=getattr(args, "confidence", None),
        role_pattern=getattr(args, "role_pattern", None),
        as_of=getattr(args, "as_of", None),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

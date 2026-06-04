"""lawvm topic — topic / keyword search across sections and HE body atoms.

Searches statute section text (sections.parquet replay_text column) and
HE body atoms (fi_he_atoms.parquet text_content column) for a keyword or
full-text query.

Usage:
    lawvm topic --topic förvaltning
    lawvm topic --topic 'ympäristö' --mode keyword
    lawvm topic --topic 'ilmasto' --statute-filter '7*/20*'
    lawvm topic --topic 'ilmasto' --mode fts
    lawvm topic --topic 'ympäristö' --as-of 2024-01-01 -o json

Keyword mode (default):
    Case-insensitive substring search using DuckDB's ILIKE / regex operators
    against sections.replay_text and fi_he_atoms.text_content.

FTS mode (--mode fts):
    DuckDB FTS extension search against pre-built FTS indexes.
    Requires 'lawvm build-index-db --fts' to have been run first and
    --db-path to point to the produced lawvm.db file.

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (fi only; 'ee'/'uk' emit a clear error)
  --as-of DATE      for sections: filter by valid_at (if column exists)
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
from lawvm.tools._cli_output import emit_rows, format_table, json_safe


# ---------------------------------------------------------------------------
# Default data-dir
# ---------------------------------------------------------------------------

_DEFAULT_DATA_DIR = "data/fi/v1"
_DEFAULT_DB_PATH = "data/fi/v1/lawvm.db"


# ---------------------------------------------------------------------------
# Schema descriptors
# ---------------------------------------------------------------------------

_SECTIONS_SCHEMA = [
    ("statute_id", "VARCHAR", "Statute ID, e.g. '711/2022'"),
    ("section_key", "VARCHAR", "Section address key, e.g. 'chapter:1/section:4'"),
    ("replay_text", "VARCHAR", "Replayed section text (up to 2000 chars)"),
    ("is_purpose_section", "BOOLEAN", "True if this is a telos/purpose section"),
    ("purpose_text_snippet", "VARCHAR", "First ~200 chars of purpose text (if applicable)"),
]

_HE_ATOMS_SCHEMA = [
    ("he_id", "VARCHAR", "HE identifier, e.g. 'HE 98/1996 vp'"),
    ("atom_id", "VARCHAR", "Body atom identifier"),
    ("atom_kind", "VARCHAR", "section|subsection|hcontainer|..."),
    ("text_content", "VARCHAR", "Atom text content"),
]


def _print_schema() -> None:
    print("\n  topic search targets:")
    print("  sections:")
    for col, typ, desc in _SECTIONS_SCHEMA:
        print(f"    {col:30s} {typ:12s} {desc}")
    print("  fi_he_atoms:")
    for col, typ, desc in _HE_ATOMS_SCHEMA:
        print(f"    {col:30s} {typ:12s} {desc}")


# ---------------------------------------------------------------------------
# Keyword-mode query
# ---------------------------------------------------------------------------


def _build_keyword_query(
    *,
    sections_path: Optional[Path],
    atoms_path: Optional[Path],
    topic: str,
    statute_filter: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
) -> str:
    """Build UNION ALL DuckDB SQL for keyword mode search.

    Searches sections.replay_text and fi_he_atoms.text_content for topic.
    Returns rows with: source, source_id, section_ref, matched_text, match_kind.
    """
    # Escape single-quotes in user-supplied topic for SQL safety
    safe_topic = topic.replace("'", "''")
    parts: List[str] = []

    # ---- sections side ----
    if sections_path is not None:
        src = source_expr_for_path(sections_path)
        conditions: List[str] = [
            f"replay_text ILIKE '%{safe_topic}%'"
        ]
        if statute_filter:
            safe_sf = statute_filter.replace("'", "''")
            # LIKE-style glob (user may use * for wildcard)
            sql_pattern = safe_sf.replace("*", "%")
            conditions.append(f"statute_id LIKE '{sql_pattern}'")
        # sections does not have a temporal column; --as-of is silently
        # inapplicable to sections (the projection is current-state only).
        where = " AND ".join(conditions)
        parts.append(
            f"SELECT 'sections' AS match_kind, "
            f"statute_id AS source_id, "
            f"section_key AS section_ref, "
            f"LEFT(replay_text, 200) AS matched_text "
            f"FROM {src} "
            f"WHERE {where}"
        )

    # ---- fi_he_atoms side ----
    if atoms_path is not None:
        src_a = source_expr_for_path(atoms_path)
        cond_a: List[str] = [
            f"text_content ILIKE '%{safe_topic}%'"
        ]
        if statute_filter:
            # HE side: filter on he_id; e.g. statute_filter="7*/20*" not meaningful
            # for HE ids, but support it anyway (user may pass he_year-like pattern)
            safe_sf = statute_filter.replace("'", "''")
            sql_pattern = safe_sf.replace("*", "%")
            cond_a.append(f"he_id LIKE '{sql_pattern}'")
        where_a = " AND ".join(cond_a)
        # he_atoms columns: check if atom_id/section_ref exists; use atom_id
        parts.append(
            f"SELECT 'fi_he_atoms' AS match_kind, "
            f"he_id AS source_id, "
            f"COALESCE(atom_id, '') AS section_ref, "
            f"LEFT(text_content, 200) AS matched_text "
            f"FROM {src_a} "
            f"WHERE {where_a}"
        )

    if not parts:
        # No sources available
        return "SELECT 'no_source' AS match_kind, '' AS source_id, '' AS section_ref, '' AS matched_text WHERE 1=0"

    union_sql = " UNION ALL ".join(parts)
    limit_clause = f" LIMIT {limit}" if limit else ""
    return (
        f"SELECT * FROM ({union_sql}) combined_results "
        f"ORDER BY match_kind, source_id, section_ref"
        f"{limit_clause}"
    )


# ---------------------------------------------------------------------------
# FTS-mode query (requires lawvm.db with FTS indexes built)
# ---------------------------------------------------------------------------


def _run_fts_query(
    *,
    db_path: str,
    topic: str,
    statute_filter: Optional[str] = None,
    limit: Optional[int] = None,
) -> None:
    """Run FTS query against a pre-built DuckDB .db file.

    Emits results to stdout in table format (FTS mode always uses table output
    since the FTS API is query-specific and can't easily be re-routed).
    """
    duckdb = require_duckdb()
    db = Path(db_path)
    if not db.exists():
        print(
            f"error: DuckDB database not found at {db_path}\n\n"
            "Run 'lawvm build-index-db --fts' first to build FTS indexes.\n"
            "Or pass --db-path to point to an existing lawvm.db.",
            file=sys.stderr,
        )
        sys.exit(1)

    safe_topic = topic.replace("'", "''")
    con = duckdb.connect(str(db), read_only=True)
    results: List[tuple] = []
    columns: List[str] = []

    # Try sections FTS
    sections_fts_sql = (
        f"SELECT 'sections' AS match_kind, "
        f"statute_id AS source_id, "
        f"section_key AS section_ref, "
        f"LEFT(replay_text, 200) AS matched_text "
        f"FROM fts_main_sections.match_bm25('{safe_topic}') "
        f"JOIN sections USING (rowid)"
    )
    atoms_fts_sql = (
        f"SELECT 'fi_he_atoms' AS match_kind, "
        f"he_id AS source_id, "
        f"COALESCE(atom_id, '') AS section_ref, "
        f"LEFT(text_content, 200) AS matched_text "
        f"FROM fts_main_fi_he_atoms.match_bm25('{safe_topic}') "
        f"JOIN fi_he_atoms USING (rowid)"
    )

    # Execute FTS queries, tolerating missing indexes gracefully (§1.8)
    for fts_sql, label in [(sections_fts_sql, "sections"), (atoms_fts_sql, "fi_he_atoms")]:
        success = False
        try:
            r = con.execute(fts_sql)
            if not columns:
                columns = [d[0] for d in r.description]
            rows = r.fetchall()
            if statute_filter and rows:
                sql_pattern = statute_filter.replace("*", "%").replace("'", "''")
                rows = [
                    row for row in rows
                    if _like_match(str(row[1]), sql_pattern)
                ]
            results.extend(rows)
            success = True
        except Exception:
            pass
        if not success:
            print(
                f"  Warning: FTS index for '{label}' not available or query failed. "
                f"Run 'lawvm build-index-db --fts' or use --mode keyword.",
                file=sys.stderr,
            )

    con.close()

    if not columns:
        columns = ["match_kind", "source_id", "section_ref", "matched_text"]

    limit_n = limit or len(results)
    results = results[:limit_n]

    from lawvm.tools._cli_output import format_table
    print(format_table(columns, results))


def _like_match(value: str, pattern: str) -> bool:
    """Simple SQL LIKE-style match (% wildcard only)."""
    import fnmatch
    return fnmatch.fnmatch(value, pattern.replace("%", "*"))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_topic(
    *,
    topic: str,
    mode: str = "keyword",
    statute_filter: Optional[str] = None,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    db_path: str = _DEFAULT_DB_PATH,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the topic search command."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm topic' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

    require_duckdb()

    if mode == "fts":
        _run_fts_query(
            db_path=db_path,
            topic=topic,
            statute_filter=statute_filter,
            limit=limit,
        )
        return

    # keyword mode
    sections_path = find_source_file(data_dir, "sections")
    atoms_path = find_source_file(data_dir, "fi_he_atoms")

    if sections_path is None and atoms_path is None:
        print(
            f"Warning: no searchable projections found in {data_dir}/\n\n"
            "Expected 'sections.parquet' and/or 'fi_he_atoms.parquet'.\n"
            "Run 'lawvm export-projections' or 'lawvm sync-fi-proposals' first.\n"
            "Or pass --data-dir to point to the projection directory.",
            file=sys.stderr,
        )
        _print_schema()
        sys.exit(1)

    query = _build_keyword_query(
        sections_path=sections_path,
        atoms_path=atoms_path,
        topic=topic,
        statute_filter=statute_filter,
        as_of=as_of,
        limit=limit,
    )

    duckdb = require_duckdb()
    con = duckdb.connect(":memory:")
    try:
        result = con.execute(query)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()

        if not rows:
            print(
                f"(0 rows) — no matches for topic {topic!r} in keyword mode.\n"
                "Try broader search, different mode (--mode fts), or check --data-dir.",
                file=sys.stderr,
            )
            # Emit empty result in requested format (§1.8 graceful empty result)
            emit_rows(
                columns=columns,
                rows=[],
                output_format=output_format,
                data_dir=data_dir,
                result_stem="_topic_query_result",
                duckdb_query=query,
                duckdb_con=con,
            )
            return

        emit_rows(
            columns=columns,
            rows=rows,
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_topic_query_result",
            duckdb_query=query,
            duckdb_con=con,
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# CLI entry point (called from cli.py)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    run_topic(
        topic=args.topic,
        mode=getattr(args, "mode", "keyword"),
        statute_filter=getattr(args, "statute_filter", None),
        as_of=getattr(args, "as_of", None),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        db_path=getattr(args, "db_path", _DEFAULT_DB_PATH),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

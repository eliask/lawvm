"""Shared DuckDB connection and data-source resolution helpers for LawVM CLI query commands.

Extracted from refs_query, actors_query, pools_query, fi_proposals_query to
avoid duplication across query modules (AGENTS.md §1.9 typed contracts, DRY).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# DuckDB availability check
# ---------------------------------------------------------------------------


def check_duckdb() -> bool:
    """Return True if duckdb is importable."""
    try:
        import duckdb  # noqa: F401  # ty: ignore[unresolved-import]
        return True
    except ImportError:
        return False


def require_duckdb() -> Any:
    """Import and return duckdb, or print a helpful error and sys.exit(1)."""
    if not check_duckdb():
        print(
            "error: duckdb is not installed.\n\n"
            "Install it with:\n"
            "  uv pip install duckdb\n",
            file=sys.stderr,
        )
        sys.exit(1)
    import duckdb  # ty: ignore[unresolved-import]
    return duckdb


# ---------------------------------------------------------------------------
# Data-source file finders
# ---------------------------------------------------------------------------


def find_source_file(data_dir: str, stem: str) -> Optional[Path]:
    """Return path to {stem}.parquet or {stem}.jsonl in data_dir, preferring Parquet.

    Args:
        data_dir: Directory to search.
        stem:     File stem without extension (e.g. "fi_refs", "sections").

    Returns:
        Path to the found file, or None if neither exists.
    """
    parquet = Path(data_dir) / f"{stem}.parquet"
    if parquet.exists():
        return parquet
    jsonl = Path(data_dir) / f"{stem}.jsonl"
    if jsonl.exists():
        return jsonl
    return None


def source_expr_for_path(path: Path) -> str:
    """Return DuckDB SQL table expression for a Parquet or JSONL file.

    Args:
        path: Path to a .parquet or .jsonl file.

    Returns:
        DuckDB SQL expression string, e.g. "read_parquet('/foo/bar.parquet')".
    """
    suffix = path.suffix.lower()
    abs_path = str(path.resolve())
    if suffix == ".parquet":
        return f"read_parquet('{abs_path}')"
    return f"read_json_auto('{abs_path}')"


# ---------------------------------------------------------------------------
# Temporal filter helpers
# ---------------------------------------------------------------------------


def as_of_conditions(as_of: str) -> list:
    """Return SQL WHERE conditions for a valid_at temporal window.

    Args:
        as_of: ISO date string, e.g. "2024-01-01".

    Returns:
        List of SQL condition strings to AND into a WHERE clause.
    """
    return [
        f"(valid_at_start IS NULL OR valid_at_start <= '{as_of}')",
        f"(valid_at_end IS NULL OR valid_at_end >= '{as_of}')",
    ]

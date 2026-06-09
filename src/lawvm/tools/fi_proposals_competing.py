"""lawvm fi-proposals-competing --statute STATUTE_ID

Detect concurrent pending government proposals (HEs) that simultaneously
amend the same statute — the "collision detection" surface.

Use case: when preparing a lausunto, knowing that 6+ HEs are concurrently
amending verotusmenettelylaki (or 2 HEs both amend ammattikorkeakoululaki)
is high-value signal: conflicting section renumbering or overlapping provision
edits may make one or both HEs legally inconsistent by the time they enact.

This command wraps a SQL GROUP-BY aggregation over fi_he_law_refs + fi_he_corpus
as a first-class CLI surface.  Without --provision-overlap it gives a flat list
of competing HEs.  With --provision-overlap it adds per-pair provision
overlap detection.

AGENTS.md compliance
--------------------
§1.9  Typed argparse args; parameter-bound DuckDB queries.
§1.10 No bare except in non-test code.
§1.8  No silent drops — filter combinations documented; empty-result path emits
      an informative message to stderr.
§1.7  No legal conflict resolved by Python accident — we surface the conflict,
      we do not resolve it.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from lawvm.tools._cli_duckdb import (
    find_source_file,
    require_duckdb,
    source_expr_for_path,
)
from lawvm.tools._cli_output import emit_rows

_DEFAULT_DATA_DIR = "data/fi/v1"


def _find_corpus_source(data_dir: str) -> Optional[Path]:
    return find_source_file(data_dir, "fi_he_corpus")


def _find_law_refs_source(data_dir: str) -> Optional[Path]:
    return find_source_file(data_dir, "fi_he_law_refs")


def _lifecycle_condition(window: str, as_of_date: str) -> tuple[str, List[Any]]:
    """Return (sql_condition, params) for the lifecycle window.

    Parameters
    ----------
    window:
        'pending' | 'active-this-year' | 'all'
    as_of_date:
        ISO date string used as reference for 'active-this-year'.

    Returns
    -------
    (sql_where_fragment, params_list)
    """
    if window == "pending":
        return "c.finlex_state = ?", ["pending"]
    if window == "active-this-year":
        this_year = int(as_of_date[:4])
        return "c.he_year = ?", [this_year]
    # 'all'
    return "1=1", []


def run_fi_proposals_competing(
    *,
    statute: str,
    as_of: Optional[str] = None,
    lifecycle_window: str = "pending",
    provision_overlap: bool = False,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run fi-proposals-competing query.

    Parameters
    ----------
    statute:
        Target statute ID, e.g. '1995/1558'.
    as_of:
        Reference date for lifecycle-window resolution (default: today).
    lifecycle_window:
        'pending' | 'active-this-year' | 'all'.
    provision_overlap:
        When True, compute pairwise provision overlap across competing HEs
        and surface as conflict_provisions column.
    limit:
        Optional row limit.
    data_dir:
        Directory containing fi_he_corpus.parquet + fi_he_law_refs.parquet.
    output_format:
        'table' | 'json' | 'jsonl' | 'csv'.
    jurisdiction:
        Must be 'fi'.
    """
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm fi-proposals-competing' only supports 'fi'; got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

    as_of_date = as_of or date.today().isoformat()

    corpus_path = _find_corpus_source(data_dir)
    if corpus_path is None:
        print(
            f"No fi_he_corpus.parquet found in {data_dir}/\n"
            "Run 'lawvm sync-fi-proposals' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    law_refs_path = _find_law_refs_source(data_dir)
    if law_refs_path is None:
        print(
            f"No fi_he_law_refs.parquet found in {data_dir}/\n"
            "Run 'lawvm sync-fi-proposals' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    duckdb = require_duckdb()

    corpus_expr = source_expr_for_path(corpus_path)
    refs_expr = source_expr_for_path(law_refs_path)

    lifecycle_cond, lifecycle_params = _lifecycle_condition(lifecycle_window, as_of_date)

    # --- Step 1: gather all competing HEs ---
    # Each row: one HE that references the target statute + its provisions touched.
    he_query = (
        f"SELECT c.he_id, c.he_year, c.ministry_show_as, c.finlex_state, "
        f"  string_agg(DISTINCT r.target_provision_ref_str, ', ') AS provisions_touched "
        f"FROM {refs_expr} r "
        f"JOIN {corpus_expr} c ON r.he_id = c.he_id "
        f"WHERE r.target_statute_id = ? "
        f"  AND {lifecycle_cond} "
        f"GROUP BY c.he_id, c.he_year, c.ministry_show_as, c.finlex_state "
        f"ORDER BY c.he_year DESC, c.he_id"
    )
    he_params: List[Any] = [statute] + lifecycle_params

    con = duckdb.connect(":memory:")
    result = con.execute(he_query, he_params)
    he_rows = result.fetchall()
    he_cols = [desc[0] for desc in result.description]

    if len(he_rows) <= 1:
        if not he_rows:
            print(
                f"No competing HEs found for statute {statute!r} "
                f"(window={lifecycle_window!r}, data_dir={data_dir!r}).",
                file=sys.stderr,
            )
        else:
            print(
                f"Only one HE found for statute {statute!r} — no competition detected.",
                file=sys.stderr,
            )
        emit_rows(
            columns=he_cols + (["conflict_provisions"] if provision_overlap else []),
            rows=[row + ("",) for row in he_rows] if (provision_overlap and he_rows) else list(he_rows),
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_fi_proposals_competing_result",
            duckdb_query=he_query,
            duckdb_con=con,
        )
        con.close()
        return

    if not provision_overlap:
        # Simple flat list: each HE is one row; no conflict_provisions column
        rows_out = he_rows
        cols_out = he_cols
        if limit:
            rows_out = rows_out[:limit]
        emit_rows(
            columns=cols_out,
            rows=rows_out,
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_fi_proposals_competing_result",
            duckdb_query=he_query,
            duckdb_con=con,
        )
        con.close()
        return

    # --- Step 2 (--provision-overlap): compute pairwise provision overlap ---
    # Build a dict: he_id -> set of provision refs it touches
    he_id_idx = he_cols.index("he_id")
    prov_idx = he_cols.index("provisions_touched")

    he_to_provisions: Dict[str, Set[str]] = {}
    for row in he_rows:
        he_id = str(row[he_id_idx])
        prov_raw = str(row[prov_idx]) if row[prov_idx] else ""
        provisions: Set[str] = set()
        for p in prov_raw.split(","):
            p = p.strip()
            if p and p != "None":
                provisions.add(p)
        he_to_provisions[he_id] = provisions

    # For each row, compute conflict_provisions = provisions it shares with any other HE
    # that also touches those provisions.
    all_provision_to_hes: Dict[str, List[str]] = {}
    for he_id, provs in he_to_provisions.items():
        for p in provs:
            all_provision_to_hes.setdefault(p, []).append(he_id)

    # conflict_provisions for a given HE = provisions where multiple HEs overlap
    rows_with_overlap: List[tuple] = []
    for row in he_rows:
        he_id = str(row[he_id_idx])
        my_provs = he_to_provisions.get(he_id, set())
        conflict_provs: Set[str] = set()
        for p in my_provs:
            competing = [h for h in all_provision_to_hes.get(p, []) if h != he_id]
            if competing:
                conflict_provs.add(p)
        rows_with_overlap.append(row + (", ".join(sorted(conflict_provs)),))

    if limit:
        rows_with_overlap = rows_with_overlap[:limit]

    cols_with_overlap = list(he_cols) + ["conflict_provisions"]
    emit_rows(
        columns=cols_with_overlap,
        rows=rows_with_overlap,
        output_format=output_format,
        data_dir=data_dir,
        result_stem="_fi_proposals_competing_result",
        duckdb_query=he_query,
        duckdb_con=con,
    )
    con.close()


def main(args: Any) -> None:
    """CLI entry point for lawvm fi-proposals-competing."""
    run_fi_proposals_competing(
        statute=getattr(args, "statute", ""),
        as_of=getattr(args, "as_of", None),
        lifecycle_window=getattr(args, "lifecycle_window", "pending") or "pending",
        provision_overlap=bool(getattr(args, "provision_overlap", False)),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

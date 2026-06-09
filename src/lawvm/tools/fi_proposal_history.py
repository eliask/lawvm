"""lawvm fi-proposal-history --statute STATUTE_ID

Show all government proposals (HEs) that have touched a given statute.

This is the most common first lausunto question: "What HEs have amended
statute X?" — currently requiring a manual SQL JOIN between fi_he_law_refs
and fi_he_corpus.  This command wraps that join as a first-class CLI surface.

Query: fi_he_law_refs JOIN fi_he_corpus ON he_id, filtered by target_statute_id.

AGENTS.md compliance
--------------------
§1.9  Typed argparse args; parameter-bound DuckDB queries (no string interpolation
      of user input into SQL).
§1.10 No bare except in non-test code.
§1.8  No silent drops — all filter combinations documented.
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
from lawvm.tools._cli_output import emit_rows, format_table

_DEFAULT_DATA_DIR = "data/fi/v1"

_LIFECYCLE_TO_STATE: dict[str, Optional[str]] = {
    "all": None,
    "pending": "pending",
    "closed": "closed",
    "enacted": "enacted",
    "rejected": "rejected",
}


def _find_corpus_source(data_dir: str) -> Optional[Path]:
    return find_source_file(data_dir, "fi_he_corpus")


def _find_law_refs_source(data_dir: str) -> Optional[Path]:
    return find_source_file(data_dir, "fi_he_law_refs")


def _find_preparatory_refs_source(data_dir: str) -> Optional[Path]:
    return find_source_file(data_dir, "fi_preparatory_refs")


def run_fi_proposal_history(
    *,
    statute: str,
    lifecycle: str = "all",
    year_range: Optional[str] = None,
    ministry: Optional[str] = None,
    include_provisions: bool = False,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run fi-proposal-history query.

    Parameters
    ----------
    statute:
        Target statute ID, e.g. '2014/527'.
    lifecycle:
        'all' | 'pending' | 'closed' | 'enacted' | 'rejected' — maps to finlex_state.
    year_range:
        Optional 'Y1:Y2' string to narrow temporal window.
    ministry:
        Optional substring filter on ministry_show_as or ministry_canonical_id.
    include_provisions:
        When True, add target_provision_ref_str to output (which provisions were touched).
    limit:
        Optional row limit.
    data_dir:
        Directory containing fi_he_corpus.parquet and fi_he_law_refs.parquet.
    output_format:
        'table' | 'json' | 'jsonl' | 'csv'.
    jurisdiction:
        Must be 'fi'.
    """
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm fi-proposal-history' only supports 'fi'; got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

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

    # Enacted-law surfacing: fi_he_corpus.finlex_state reflects only the HE's
    # OWN self-reported lifecycle (Finlex can keep an HE at "pending" long after
    # its law is in force). fi_preparatory_refs is the INVERSE link — each
    # enacted statute cites the HE that produced it (kind='he') — so we can
    # surface the enacted law(s) for an HE even when its self-state is still
    # "pending". LEFT JOIN so it degrades gracefully when the projection is
    # absent (the column shows NULL, never a wrong value).
    prep_path = _find_preparatory_refs_source(data_dir)
    has_prep = prep_path is not None
    enacted_subquery = ""
    enacted_join = ""
    if has_prep:
        prep_expr = source_expr_for_path(prep_path)
        enacted_subquery = (
            "enacted AS ("
            "  SELECT he_year, he_number, "
            "         string_agg(DISTINCT source_statute_id, ', ' "
            "                    ORDER BY source_statute_id) AS enacted_as "
            f"  FROM {prep_expr} "
            "  WHERE kind = 'he' AND source_statute_id IS NOT NULL "
            "  GROUP BY he_year, he_number"
            ")"
        )
        enacted_join = (
            "LEFT JOIN enacted e "
            "ON e.he_year = c.he_year AND e.he_number = c.he_number "
        )

    # Base columns always present
    enacted_col = ("e.enacted_as",) if has_prep else ()
    if include_provisions:
        cols = (
            "c.he_id",
            "c.he_year",
            "c.ministry_show_as",
            "c.title",
            "c.finlex_state",
        ) + enacted_col + (
            "r.target_provision_ref_str AS provisions_touched",
        )
        group_cols = (
            "c.he_id",
            "c.he_year",
            "c.he_number",
            "c.ministry_show_as",
            "c.title",
            "c.finlex_state",
        ) + enacted_col + (
            "r.target_provision_ref_str",
        )
    else:
        cols = (
            "c.he_id",
            "c.he_year",
            "c.ministry_show_as",
            "c.title",
            "c.finlex_state",
        ) + enacted_col
        group_cols = (
            "c.he_id",
            "c.he_year",
            "c.he_number",
            "c.ministry_show_as",
            "c.title",
            "c.finlex_state",
        ) + enacted_col

    select_clause = ", ".join(cols)
    group_by_clause = "GROUP BY " + ", ".join(group_cols)

    # Build WHERE conditions using parameter binding where possible.
    # DuckDB supports positional parameters with ?; we collect them in order.
    conditions: List[str] = ["r.target_statute_id = ?"]
    params: List[Any] = [statute]

    finlex_state = _LIFECYCLE_TO_STATE.get(lifecycle)
    if finlex_state is not None:
        conditions.append("c.finlex_state = ?")
        params.append(finlex_state)

    if year_range:
        parts = year_range.split(":")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            conditions.append("c.he_year BETWEEN ? AND ?")
            params.append(int(parts[0]))
            params.append(int(parts[1]))

    if ministry:
        # LIKE pattern — must be injected as param, not interpolated
        conditions.append(
            "(lower(c.ministry_show_as) LIKE lower(?) "
            "OR lower(c.ministry_canonical_id) LIKE lower(?))"
        )
        ministry_pat = f"%{ministry}%"
        params.append(ministry_pat)
        params.append(ministry_pat)

    where_clause = "WHERE " + " AND ".join(conditions)
    limit_clause = f"LIMIT {limit}" if limit else ""

    with_clause = f"WITH {enacted_subquery} " if enacted_subquery else ""
    query = (
        f"{with_clause}"
        f"SELECT {select_clause} "
        f"FROM {refs_expr} r "
        f"JOIN {corpus_expr} c ON r.he_id = c.he_id "
        f"{enacted_join}"
        f"{where_clause} "
        # One HE can touch multiple provisions in the same statute. Collapse
        # those duplicate HE rows unless provisions are part of the output.
        f"{group_by_clause} "
        f"ORDER BY c.he_year DESC, c.he_number DESC "
        f"{limit_clause}"
    )

    con = duckdb.connect(":memory:")
    result = con.execute(query, params)
    columns = [desc[0] for desc in result.description]
    rows = result.fetchall()

    if not rows:
        print(
            f"No HEs found touching statute {statute!r} "
            f"(lifecycle={lifecycle!r}, data_dir={data_dir!r})",
            file=sys.stderr,
        )
        emit_rows(
            columns=columns,
            rows=rows,
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_fi_proposal_history_result",
            duckdb_query=query,
            duckdb_con=con,
        )
        con.close()
        return

    emit_rows(
        columns=columns,
        rows=rows,
        output_format=output_format,
        data_dir=data_dir,
        result_stem="_fi_proposal_history_result",
        duckdb_query=query,
        duckdb_con=con,
    )
    con.close()


def main(args: Any) -> None:
    """CLI entry point for lawvm fi-proposal-history."""
    run_fi_proposal_history(
        statute=getattr(args, "statute", ""),
        lifecycle=getattr(args, "lifecycle", "all") or "all",
        year_range=getattr(args, "year_range", None),
        ministry=getattr(args, "ministry", None),
        include_provisions=bool(getattr(args, "include_provisions", False)),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

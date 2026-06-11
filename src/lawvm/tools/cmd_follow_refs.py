"""lawvm follow-refs — multi-hop reference traversal over fi_refs.parquet.

Traverses the citation graph from a starting provision reference up to --depth
hops. Returns an edge list annotated with hop depth.

Usage:
    lawvm follow-refs --start 711/2022/7
    lawvm follow-refs --start 711/2022 --depth 2
    lawvm follow-refs --start 711/2022 --direction reverse
    lawvm follow-refs --start 711/2022 --direction both --depth 3
    lawvm follow-refs --start 711/2022 --include-broken -o json

Direction semantics:
    forward  (default) — follow outgoing references (this statute cites others)
    reverse             — follow incoming references (who cites this statute)
    both                — traverse in both directions

Backed by fi_refs.parquet (statute-side) and optionally fi_he_law_refs.parquet
(HE-side, included when --include-he is set).

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags
  -j JURISDICTION   (fi only)
  --as-of DATE      filter ref validity window
  -o {table|json|jsonl|csv|parquet}
  --limit N
  --data-dir PATH
"""
from __future__ import annotations

import sys
from collections import deque
from typing import Any, List, Optional, Set, Tuple

from lawvm.tools._cli_duckdb import (
    as_of_conditions,
    find_source_file,
    require_duckdb,
    source_expr_for_path,
)
from lawvm.tools._cli_output import emit_rows


_DEFAULT_DATA_DIR = "data/fi/v1"

# Columns in the edge-list output
_EDGE_COLUMNS = [
    "depth",
    "direction",
    "source_statute_id",
    "source_provision_ref_str",
    "target_statute_id",
    "target_provision_ref_str",
    "cite_kind",
    "cite_confidence",
    "valid_at_start",
    "valid_at_end",
]


def _load_refs_for_node(
    *,
    con: Any,
    refs_expr: str,
    node_ref: str,
    direction: str,
    as_of: Optional[str],
    include_broken: bool,
) -> List[Tuple[Any, ...]]:
    """Fetch one hop of edges from the ref graph for node_ref.

    Args:
        con:           Open DuckDB connection.
        refs_expr:     DuckDB table expression for fi_refs.
        node_ref:      Provision ref string to start from (statute_id or provision_ref_str).
        direction:     "forward" | "reverse" | "both".
        as_of:         Optional temporal filter date.
        include_broken: If False, exclude rows where cite_confidence = 'broken'.

    Returns:
        List of raw edge tuples (source_statute_id, source_provision_ref_str,
        target_statute_id, target_provision_ref_str, cite_kind, cite_confidence,
        valid_at_start, valid_at_end).
    """
    # node_ref may be a statute_id (YYYY/NNN) or a provision_ref_str (YYYY/NNN/...)
    # We match on both columns to be permissive.
    safe_ref = node_ref.replace("'", "''")
    conditions: List[str] = []
    fwd_cond = (
        f"(source_statute_id = '{safe_ref}' OR "
        f"source_provision_ref_str = '{safe_ref}' OR "
        f"source_provision_ref_str LIKE '{safe_ref}/%')"
    )
    rev_cond = (
        f"(target_statute_id = '{safe_ref}' OR "
        f"target_provision_ref_str = '{safe_ref}' OR "
        f"target_provision_ref_str LIKE '{safe_ref}/%')"
    )

    if direction == "forward":
        conditions.append(fwd_cond)
    elif direction == "reverse":
        conditions.append(rev_cond)
    else:  # both
        conditions.append(f"({fwd_cond} OR {rev_cond})")

    if not include_broken:
        conditions.append("cite_confidence != 'broken'")

    if as_of:
        conditions.extend(as_of_conditions(as_of))

    where = " AND ".join(conditions)
    sql = (
        f"SELECT "
        f"source_statute_id, source_provision_ref_str, "
        f"target_statute_id, target_provision_ref_str, "
        f"cite_kind, cite_confidence, valid_at_start, valid_at_end "
        f"FROM {refs_expr} "
        f"WHERE {where}"
    )
    result = con.execute(sql)
    return result.fetchall()


def _traverse(
    *,
    con: Any,
    refs_expr: str,
    start: str,
    depth: int,
    direction: str,
    as_of: Optional[str],
    include_broken: bool,
    limit: Optional[int],
) -> List[tuple[Any, ...]]:
    """BFS traversal of the citation graph.

    Returns an edge list with depth annotation. Each row in the output has:
    (depth, direction_label, source_statute_id, source_provision_ref_str,
     target_statute_id, target_provision_ref_str, cite_kind, cite_confidence,
     valid_at_start, valid_at_end)
    """
    output: List[tuple[Any, ...]] = []
    seen_edges: Set[tuple[Any, ...]] = set()
    # Queue: (node_ref, current_depth)
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    visited_nodes: Set[str] = {start}

    while queue:
        node, current_depth = queue.popleft()
        if current_depth >= depth:
            continue

        edges = _load_refs_for_node(
            con=con,
            refs_expr=refs_expr,
            node_ref=node,
            direction=direction,
            as_of=as_of,
            include_broken=include_broken,
        )

        for edge in edges:
            (src_stat, src_prov, tgt_stat, tgt_prov,
             cite_kind, cite_conf, v_start, v_end) = edge

            edge_key = (src_stat, src_prov, tgt_stat, tgt_prov)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            # Determine edge direction relative to start
            edge_dir = _edge_direction(
                node=node,
                src_stat=src_stat,
                src_prov=src_prov,
                tgt_stat=tgt_stat,
                tgt_prov=tgt_prov,
            )

            output.append((
                current_depth + 1,
                edge_dir,
                src_stat,
                src_prov,
                tgt_stat,
                tgt_prov,
                cite_kind,
                cite_conf,
                v_start,
                v_end,
            ))

            # Enqueue the other end for next hop
            if current_depth + 1 < depth:
                next_node = tgt_stat if edge_dir == "forward" else src_stat
                if next_node and next_node not in visited_nodes:
                    visited_nodes.add(next_node)
                    queue.append((next_node, current_depth + 1))

        if limit and len(output) >= limit:
            break

    if limit:
        output = output[:limit]

    return output


def _edge_direction(
    *,
    node: str,
    src_stat: str,
    src_prov: Optional[str],
    tgt_stat: str,
    tgt_prov: Optional[str],
) -> str:
    """Classify an edge as forward (outgoing from node) or reverse (incoming)."""
    node_clean = node.split("/")[0] + "/" + node.split("/")[1] if "/" in node else node
    src_stat_clean = (src_stat or "").split("/")
    src_key = "/".join(src_stat_clean[:2]) if len(src_stat_clean) >= 2 else (src_stat or "")

    if src_key == node_clean or (src_prov or "").startswith(node):
        return "forward"
    return "reverse"


def run_follow_refs(
    *,
    start: str,
    depth: int = 1,
    direction: str = "forward",
    include_broken: bool = False,
    as_of: Optional[str] = None,
    limit: Optional[int] = None,
    data_dir: str = _DEFAULT_DATA_DIR,
    output_format: str = "table",
    jurisdiction: str = "fi",
) -> None:
    """Run the follow-refs traversal command."""
    if jurisdiction != "fi":
        print(
            f"error: 'lawvm follow-refs' currently only supports jurisdiction 'fi'; "
            f"got {jurisdiction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Using projections from {data_dir}/ (override with --data-dir)", file=sys.stderr)

    if depth < 1:
        print("error: --depth must be at least 1", file=sys.stderr)
        sys.exit(1)

    if direction not in ("forward", "reverse", "both"):
        print(
            f"error: --direction must be one of forward|reverse|both; "
            f"got {direction!r}",
            file=sys.stderr,
        )
        sys.exit(1)

    refs_path = find_source_file(data_dir, "fi_refs")
    if refs_path is None:
        print(
            f"No fi_refs.parquet or fi_refs.jsonl found in {data_dir}/\n\n"
            "Run 'lawvm export-projections' first to generate fi_refs.\n"
            "Or pass --data-dir to point to the projection directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    duckdb = require_duckdb()
    refs_expr = source_expr_for_path(refs_path)

    con = duckdb.connect(":memory:")
    try:
        rows = _traverse(
            con=con,
            refs_expr=refs_expr,
            start=start,
            depth=depth,
            direction=direction,
            as_of=as_of,
            include_broken=include_broken,
            limit=limit,
        )

        # Build a representative DuckDB query for parquet output mode
        dummy_query = (
            "SELECT 0 AS depth, 'forward' AS direction, "
            "'' AS source_statute_id, '' AS source_provision_ref_str, "
            "'' AS target_statute_id, '' AS target_provision_ref_str, "
            "'' AS cite_kind, '' AS cite_confidence, "
            "NULL AS valid_at_start, NULL AS valid_at_end "
            "WHERE 1=0"
        )

        if not rows:
            print(
                f"(0 rows) — no references found from {start!r} at depth={depth}, "
                f"direction={direction}.\n"
                "Check --start, --direction, --include-broken, or --data-dir.",
                file=sys.stderr,
            )

        emit_rows(
            columns=_EDGE_COLUMNS,
            rows=rows,
            output_format=output_format,
            data_dir=data_dir,
            result_stem="_follow_refs_result",
            duckdb_query=dummy_query,
            duckdb_con=con,
        )
    finally:
        con.close()


def main(args: Any) -> None:
    run_follow_refs(
        start=args.start,
        depth=getattr(args, "depth", 1),
        direction=getattr(args, "direction", "forward"),
        include_broken=getattr(args, "include_broken", False),
        as_of=getattr(args, "as_of", None),
        limit=getattr(args, "limit", None),
        data_dir=getattr(args, "data_dir", _DEFAULT_DATA_DIR),
        output_format=getattr(args, "output_format", "table"),
        jurisdiction=getattr(args, "jurisdiction", "fi"),
    )

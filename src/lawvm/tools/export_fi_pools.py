"""Export fi_pools.parquet -- PoolMention projection for Finland.

Produces fi_pools.parquet (and fi_pools.jsonl fallback) by running
extract_pool_mentions over each statute in the corpus.

This module is called by export_parquet.main() when --include-pools is passed,
and also available as a standalone entry point.

Schema: per POOL_MENTION_EXTRACTION.md §Projection export.

Usage (standalone):
    python -m lawvm.tools.export_fi_pools --data-dir .tmp/projections

Called from export_parquet:
    export_fi_pools(corpus, data_dir=..., use_parquet=True)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.pool_mention import pool_mention_to_row


def _load_corpus_store() -> Any:
    """Load the Finland consolidated corpus store for XML acquisition."""
    from lawvm.finland.corpus import get_corpus_store
    return get_corpus_store()


def _get_statute_xml(statute_id: str, store: Any) -> Optional[bytes]:
    """Get XML bytes for a statute from the corpus store."""
    try:
        return store.get_xml(statute_id)
    except Exception:
        return None


def _project_pools_for_statute(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project PoolMention rows for one statute.

    Returns (mention_rows, diagnostic_rows).
    """
    from lawvm.finland.pool_mention_extractor import extract_pool_mentions

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    result = extract_pool_mentions(xml_bytes, statute_id)

    mention_rows: List[Dict[str, Any]] = []
    for mention in result.mentions:
        row = pool_mention_to_row(mention)
        row["source_statute_id"] = statute_id
        mention_rows.append(row)

    # Emit diagnostics for audit trail (AGENTS.md §1.8)
    diag_rows: List[Dict[str, Any]] = []

    for rej in result.rejected:
        diag_rows.append({
            "statute_id": statute_id,
            "kind": "rejected_pool_candidate",
            "rule_id": rej.rule_id,
            "phase": rej.phase,
            "reason": rej.reason,
            "matched_text": rej.matched_text,
            "blocking": rej.blocking,
        })

    for af in result.ambiguous_findings:
        diag_rows.append({
            "statute_id": statute_id,
            "kind": "ambiguous_pool_mention",
            "rule_id": af.rule_id,
            "phase": af.phase,
            "quantity_phrase": af.quantity_phrase,
            "candidate_ids": list(af.candidate_canonical_ids),
            "reason": af.reason,
            "blocking": af.blocking,
        })

    for obs in result.renumbering_observations:
        diag_rows.append({
            "statute_id": statute_id,
            "kind": "budget_line_renumbering_observation",
            "rule_id": obs.rule_id,
            "phase": obs.phase,
            "quantity_phrase": obs.quantity_phrase,
            "original_canonical_id": obs.original_canonical_id,
            "resolved_canonical_id": obs.resolved_canonical_id,
            "lineage_year": obs.lineage_year,
            "resolution_year": obs.resolution_year,
            "reason": obs.reason,
        })

    return mention_rows, diag_rows


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    """Write rows as JSONL, return count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def _try_write_parquet(path: Path, rows: List[Dict[str, Any]]) -> bool:
    """Try to write rows as Parquet. Returns True if successful."""
    try:
        import pyarrow as pa  # ty: ignore[unresolved-import]
        import pyarrow.parquet as pq  # ty: ignore[unresolved-import]
    except ImportError:
        return False

    if not rows:
        # Write empty parquet with schema for schema-stability
        schema = pa.schema([
            pa.field("source_statute_id", pa.string()),
            pa.field("source_provision_ref_str", pa.string()),
            pa.field("quantity_phrase", pa.string()),
            pa.field("pool_canonical_id", pa.string()),
            pa.field("quantity_kind", pa.string()),
            pa.field("resolution_confidence", pa.string()),
            pa.field("numeric_value", pa.float64()),
            pa.field("unit", pa.string()),
            pa.field("source_span_file", pa.string()),
            pa.field("source_span_byte_offset", pa.int64()),
            pa.field("source_span_byte_len", pa.int64()),
            pa.field("valid_at_start", pa.string()),
            pa.field("valid_at_end", pa.string()),
        ])
        table = pa.table({col: [] for col in schema.names}, schema=schema)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(path), compression="zstd")
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, str(path), compression="zstd")
    return True


def export_fi_pools(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
) -> int:
    """Export fi_pools.parquet projection for a corpus of Finnish statutes.

    Args:
        corpus:      List of (amendment_count, statute_id) tuples.
        data_dir:    Output directory. fi_pools.parquet written here.
        use_parquet: Write Parquet if pyarrow available (also writes JSONL).
        limit:       Process only first N statutes (for testing).

    Returns:
        Number of PoolMention rows written.
    """
    store = None
    try:
        store = _load_corpus_store()
    except Exception as exc:
        print(f"  warning: could not load corpus store: {exc}", file=sys.stderr)
        return 0

    if limit:
        corpus = corpus[:limit]

    total = len(corpus)
    all_mention_rows: List[Dict[str, Any]] = []
    all_diag_rows: List[Dict[str, Any]] = []

    for i, (_, statute_id) in enumerate(corpus, 1):
        t0 = time.time()
        mention_rows, diag_rows = _project_pools_for_statute(statute_id, store)
        all_mention_rows.extend(mention_rows)
        all_diag_rows.extend(diag_rows)

        if i % 50 == 0 or i == total:
            elapsed = time.time() - t0
            print(f"  [{i}/{total}] pools: {len(all_mention_rows):,} total ({elapsed:.1f}s last)")

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Always write JSONL (DuckDB can read it)
    jsonl_count = _write_jsonl(out / "fi_pools.jsonl", all_mention_rows)

    if use_parquet:
        ok = _try_write_parquet(out / "fi_pools.parquet", all_mention_rows)
        if ok:
            print(f"  fi_pools: {jsonl_count:,} rows (Parquet + JSONL)")
        else:
            print(f"  fi_pools: {jsonl_count:,} rows (JSONL only; pyarrow not installed)")
    else:
        print(f"  fi_pools: {jsonl_count:,} rows (JSONL)")

    # Write diagnostics for audit trail
    if all_diag_rows:
        _write_jsonl(out / "fi_pools_diagnostics.jsonl", all_diag_rows)

    return jsonl_count

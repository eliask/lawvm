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

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.finland.pool_mention_primitive import pool_mention_to_row


def _load_corpus_store() -> Any:
    """Load the Finland consolidated corpus store for XML acquisition."""
    from lawvm.finland.corpus import get_corpus_store
    return get_corpus_store()


def _get_statute_xml(statute_id: str, store: Any) -> Optional[bytes]:
    """Get XML bytes for a statute from the corpus store."""
    try:
        return store.read_oracle(statute_id)
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
        row: Dict[str, Any] = dict(pool_mention_to_row(mention))
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
    from lawvm.tools.export_persistence import write_jsonl

    return write_jsonl(path, rows)


def _attach_compile_metadata(table: Any, compile_metadata: Any) -> Any:
    """Attach CompileMetadata fields to a pyarrow Table's schema metadata."""
    if compile_metadata is None:
        raise ValueError(
            "export_fi_pools: CompileMetadata is required for v3 substrate-locked "
            "persistence. Construct via build_default_compile_metadata() or "
            "explicitly. See UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13 Step 5."
        )
    existing = table.schema.metadata or {}
    meta = dict(existing)
    for k, v in compile_metadata.to_metadata_dict().items():
        meta[k.encode()] = v.encode()
    return table.replace_schema_metadata(meta)


def _try_write_parquet(
    path: Path,
    rows: List[Dict[str, Any]],
    compile_metadata: Any = None,
) -> bool:
    """Try to write rows as Parquet with optional compile metadata. Returns True if ok."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
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
        table = _attach_compile_metadata(table, compile_metadata)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(path), compression="zstd")
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    table = _attach_compile_metadata(table, compile_metadata)
    pq.write_table(table, str(path), compression="zstd")
    return True


def export_fi_pools(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Export fi_pools.parquet projection for a corpus of Finnish statutes.

    Args:
        corpus:      List of (amendment_count, statute_id) tuples.
        data_dir:    Output directory. fi_pools.parquet written here.
        use_parquet: Write Parquet if pyarrow available (also writes JSONL).
        limit:       Process only first N statutes (for testing).
        workers:     Parallel worker processes (0 = auto; 1 = serial). Rows are
                     reassembled in corpus order so output is byte-identical
                     regardless of worker count.

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

    from lawvm.tools._parallel_corpus import project_corpus_parallel

    statute_ids = [sid for _, sid in corpus]
    all_mention_rows, all_diag_rows = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=(__name__, "_project_pools_for_statute"),
        serial_projector=_project_pools_for_statute,
        store=store,
        workers=workers,
    )
    print(f"  pools: {len(all_mention_rows):,} mention rows over {len(statute_ids):,} statutes")

    from lawvm.tools.export_persistence import export_projection_tail

    if use_parquet and compile_metadata is not None:
        return export_projection_tail(
            name="fi_pools",
            data_dir=data_dir,
            rows=all_mention_rows,
            diag_rows=all_diag_rows,
            use_parquet=True,
            compile_metadata=compile_metadata,
            statute_count=len(statute_ids),
        ).row_count

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    jsonl_count = _write_jsonl(out / "fi_pools.jsonl", all_mention_rows)

    if use_parquet:
        ok = _try_write_parquet(out / "fi_pools.parquet", all_mention_rows, compile_metadata)
        if ok:
            print(f"  fi_pools: {jsonl_count:,} rows (Parquet + JSONL)")
        else:
            print(f"  fi_pools: {jsonl_count:,} rows (JSONL only; pyarrow not installed)")
    else:
        print(f"  fi_pools: {jsonl_count:,} rows (JSONL)")

    if all_diag_rows:
        _write_jsonl(out / "fi_pools_diagnostics.jsonl", all_diag_rows)

    return jsonl_count

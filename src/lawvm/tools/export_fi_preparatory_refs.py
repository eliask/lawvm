"""Export fi_preparatory_refs.parquet — PreparatoryReference projection for Finland.

Produces fi_preparatory_refs.parquet (and fi_preparatory_refs.jsonl fallback) by
running extract_preparatory_refs over each statute in the corpus.

This module is called by export_parquet.main() when --include-preparatory-refs is
passed, and also available as a standalone entry point.

Schema: per PREPARATORY_REFERENCE_SWEEP.md §Projection export.

Usage (standalone):
    python -m lawvm.tools.export_fi_preparatory_refs --data-dir .tmp/projections

Called from export_parquet:
    export_fi_preparatory_refs(corpus, data_dir=..., use_parquet=True)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.preparatory_reference import preparatory_reference_to_row


def _load_corpus_store() -> Any:
    """Load the Finland consolidated corpus store for XML acquisition."""
    from lawvm.finland.corpus import get_corpus_store
    return get_corpus_store()


def _get_statute_xml(statute_id: str, store: Any) -> Optional[bytes]:
    """Get XML bytes for a statute from the corpus store.

    Returns None if the statute is not available.
    """
    xml = store.read_oracle(statute_id)
    return xml


def _project_preparatory_refs_for_statute(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project PreparatoryReference rows for one statute.

    Returns (ref_rows, diagnostic_rows).
    """
    from lawvm.finland.preparatory_reference_extractor import extract_preparatory_refs

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    result = extract_preparatory_refs(xml_bytes, statute_id)

    ref_rows = [preparatory_reference_to_row(r) for r in result.refs]

    # Emit diagnostics for audit trail
    diag_rows: List[Dict[str, Any]] = []

    for rej in result.rejected:
        diag_rows.append({
            "statute_id": statute_id,
            "kind": "rejected_preparatory_candidate",
            "rule_id": rej.rule_id,
            "phase": rej.phase,
            "reason": rej.reason,
            "raw_text": rej.raw_text[:200],
            "blocking": rej.blocking,
        })

    for obs in result.lifecycle_observations:
        diag_rows.append({
            "statute_id": statute_id,
            "kind": "committee_lifecycle_observation",
            "rule_id": obs.rule_id,
            "phase": obs.phase,
            "committee_abbrev": obs.committee_abbrev,
            "canonical_id": obs.canonical_id,
            "lifecycle_event": obs.lifecycle_event,
        })

    return ref_rows, diag_rows


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    """Write rows as JSONL, return count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def _attach_compile_metadata(table: Any, compile_metadata: Any) -> Any:
    """Attach CompileMetadata fields to a pyarrow Table's schema metadata."""
    if compile_metadata is None:
        raise ValueError(
            "export_fi_preparatory_refs: CompileMetadata is required for v3 substrate-locked "
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
        import pyarrow as pa  # ty: ignore[unresolved-import]
        import pyarrow.parquet as pq  # ty: ignore[unresolved-import]
    except ImportError:
        return False

    if not rows:
        # Write empty parquet with schema for schema-stability
        schema = pa.schema([
            pa.field("source_statute_id", pa.string()),
            pa.field("kind", pa.string()),
            pa.field("canonical_id", pa.string()),
            pa.field("raw_text", pa.string()),
            pa.field("committee_abbrev", pa.string()),
            pa.field("he_year", pa.int32()),
            pa.field("he_number", pa.int32()),
            pa.field("eu_form", pa.string()),
            pa.field("eu_number", pa.int32()),
            pa.field("eu_year", pa.int32()),
            pa.field("celex", pa.string()),
            pa.field("oj_series", pa.string()),
            pa.field("oj_number", pa.int32()),
            pa.field("oj_date", pa.string()),
            pa.field("oj_page", pa.int32()),
            pa.field("confidence", pa.string()),
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


def export_fi_preparatory_refs(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Export fi_preparatory_refs.parquet projection for a corpus of Finnish statutes.

    Args:
        corpus:      List of (amendment_count, statute_id) tuples.
        data_dir:    Output directory. fi_preparatory_refs.parquet written here.
        use_parquet: Write Parquet if pyarrow available (also writes JSONL).
        limit:       Process only first N statutes (for testing).
        workers:     Parallel worker processes (0 = auto; 1 = serial). Rows are
                     reassembled in corpus order so output is byte-identical
                     regardless of worker count.

    Returns:
        Number of PreparatoryReference rows written.
    """
    store = None
    store = _load_corpus_store()

    if limit:
        corpus = corpus[:limit]

    from lawvm.tools._parallel_corpus import project_corpus_parallel

    statute_ids = [sid for _, sid in corpus]
    all_ref_rows, all_diag_rows = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=(__name__, "_project_preparatory_refs_for_statute"),
        serial_projector=_project_preparatory_refs_for_statute,
        store=store,
        workers=workers,
    )
    print(
        f"  preparatory_refs: {len(all_ref_rows):,} ref rows over "
        f"{len(statute_ids):,} statutes"
    )

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Always write JSONL (DuckDB can read it)
    jsonl_count = _write_jsonl(out / "fi_preparatory_refs.jsonl", all_ref_rows)

    if use_parquet:
        ok = _try_write_parquet(out / "fi_preparatory_refs.parquet", all_ref_rows, compile_metadata)
        if ok:
            print(f"  fi_preparatory_refs: {jsonl_count:,} rows (Parquet + JSONL)")
        else:
            print(
                f"  fi_preparatory_refs: {jsonl_count:,} rows "
                f"(JSONL only; pyarrow not installed)"
            )
    else:
        print(f"  fi_preparatory_refs: {jsonl_count:,} rows (JSONL)")

    # Write diagnostics for audit trail
    if all_diag_rows:
        _write_jsonl(
            out / "fi_preparatory_refs_diagnostics.jsonl", all_diag_rows
        )

    return jsonl_count

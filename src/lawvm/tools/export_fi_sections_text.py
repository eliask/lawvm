"""Export fi_sections_text.parquet — oracle section-text projection for Finland.

Produces fi_sections_text.parquet (and fi_sections_text.jsonl fallback) by
walking the consolidated AKN oracle for each statute and extracting per-section
text via section_text_extractor.extract_sections_text.

This module is called by:
  - export_parquet.main() when --include-sections-text is passed;
  - rebuild_indexes (via _dispatch_projection) for the fi_sections_text spec;
  - directly as a standalone entry point.

Schema: per SECTIONS_TEXT_PROJECTION.md §Typed primitive.

Emitter pattern: follows export_fi_refs.py.

Usage (standalone):
    python -m lawvm.tools.export_fi_sections_text --data-dir data/fi/v1

Called from export_parquet:
    export_fi_sections_text(corpus, data_dir=..., use_parquet=True)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.section_text import section_text_to_row


# ---------------------------------------------------------------------------
# Parquet schema column spec (pinned for schema-stability tests).
# pyarrow is imported lazily (optional dependency).
# ---------------------------------------------------------------------------

# Column names and types as a plain tuple — importable without pyarrow.
FI_SECTIONS_TEXT_COLUMNS = (
    "statute_id",
    "section_key",
    "section_label",
    "heading_text",
    "body_text",
    "char_count",
    "source_span_byte_offset",
    "source_span_len",
    "valid_at_start",
    "valid_at_end",
)


def _make_parquet_schema() -> Any:
    """Build a pyarrow.Schema for fi_sections_text. Requires pyarrow."""
    import pyarrow as pa  # ty: ignore[unresolved-import]
    return pa.schema([
        pa.field("statute_id", pa.string()),
        pa.field("section_key", pa.string()),
        pa.field("section_label", pa.string()),
        pa.field("heading_text", pa.string()),
        pa.field("body_text", pa.string()),
        pa.field("char_count", pa.int64()),
        pa.field("source_span_byte_offset", pa.int64()),
        pa.field("source_span_len", pa.int64()),
        pa.field("valid_at_start", pa.string()),
        pa.field("valid_at_end", pa.string()),
    ])


# Lazy singleton — only populated when pyarrow is available
_FI_SECTIONS_TEXT_SCHEMA: Any = None


def get_parquet_schema() -> Any:
    """Return (and cache) the pyarrow schema. Raises ImportError if pyarrow absent."""
    global _FI_SECTIONS_TEXT_SCHEMA
    if _FI_SECTIONS_TEXT_SCHEMA is None:
        _FI_SECTIONS_TEXT_SCHEMA = _make_parquet_schema()
    return _FI_SECTIONS_TEXT_SCHEMA


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_corpus_store() -> Any:
    """Load the Finland consolidated corpus store for XML acquisition."""
    from lawvm.finland.corpus import get_corpus_store
    return get_corpus_store()


def _get_oracle_xml(statute_id: str, store: Any) -> Optional[bytes]:
    """Return oracle XML bytes for a statute, or None if unavailable."""
    try:
        return store.read_oracle(statute_id)
    except Exception:
        return None


def _project_sections_for_statute(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project SectionText rows for one statute.

    Returns (section_rows, diagnostic_rows).
    """
    from lawvm.finland.section_text_extractor import extract_sections_text

    xml_bytes = _get_oracle_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    result = extract_sections_text(xml_bytes, statute_id)

    section_rows = [section_text_to_row(s) for s in result.sections]

    diag_rows: List[Dict[str, Any]] = []
    for diag in result.diagnostics:
        diag_rows.append({
            "statute_id": statute_id,
            "rule_id": diag.rule_id,
            "phase": diag.phase,
            "reason": diag.reason,
            "blocking": diag.blocking,
        })

    return section_rows, diag_rows


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
            "export_fi_sections_text: CompileMetadata is required for v3 substrate-locked "
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
    """Try to write rows as Parquet with zstd compression and optional compile metadata.

    Returns True if successful.
    """
    try:
        import pyarrow as pa  # ty: ignore[unresolved-import]
        import pyarrow.parquet as pq  # ty: ignore[unresolved-import]
    except ImportError:
        return False

    schema = get_parquet_schema()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        table = pa.table(
            {col: [] for col in FI_SECTIONS_TEXT_COLUMNS},
            schema=schema,
        )
        table = _attach_compile_metadata(table, compile_metadata)
        pq.write_table(table, str(path), compression="zstd")
        return True

    table = pa.Table.from_pylist(rows, schema=schema)
    table = _attach_compile_metadata(table, compile_metadata)
    pq.write_table(table, str(path), compression="zstd")
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def export_fi_sections_text(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Export fi_sections_text.parquet projection for a corpus of Finnish statutes.

    Args:
        corpus:      List of (amendment_count, statute_id) tuples.
        data_dir:    Output directory. fi_sections_text.parquet written here.
        use_parquet: Write Parquet if pyarrow available (also writes JSONL).
        limit:       Process only first N statutes (for testing).
        workers:     Parallel worker processes (0 = auto; 1 = serial). Rows are
                     reassembled in corpus order so output is byte-identical
                     regardless of worker count.

    Returns:
        Number of SectionText rows written.
    """
    store = None
    try:
        store = _load_corpus_store()
    except Exception as exc:
        print(f"  warning: could not load corpus store: {exc}", file=sys.stderr)
        return 0

    if limit:
        corpus = corpus[:limit]

    t_start = time.time()

    from lawvm.tools._parallel_corpus import project_corpus_parallel

    statute_ids = [sid for _, sid in corpus]
    all_section_rows, all_diag_rows = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=(__name__, "_project_sections_for_statute"),
        serial_projector=_project_sections_for_statute,
        store=store,
        workers=workers,
    )
    _rate_elapsed = time.time() - t_start
    _rate = len(statute_ids) / _rate_elapsed if _rate_elapsed > 0 else 0
    print(
        f"  sections: {len(all_section_rows):,} rows over {len(statute_ids):,} "
        f"statutes ({_rate:.0f} statutes/s)"
    )

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Always write JSONL (DuckDB can read it directly)
    jsonl_count = _write_jsonl(out / "fi_sections_text.jsonl", all_section_rows)

    parquet_written = False
    if use_parquet:
        ok = _try_write_parquet(out / "fi_sections_text.parquet", all_section_rows, compile_metadata)
        if ok:
            parquet_written = True
            print(
                f"  fi_sections_text: {jsonl_count:,} rows "
                f"(Parquet+zstd + JSONL)"
            )
        else:
            print(
                f"  fi_sections_text: {jsonl_count:,} rows "
                f"(JSONL only; pyarrow not installed)"
            )
    else:
        print(f"  fi_sections_text: {jsonl_count:,} rows (JSONL)")

    # Diagnostics audit trail
    if all_diag_rows:
        _write_jsonl(out / "fi_sections_text_diagnostics.jsonl", all_diag_rows)
        print(f"  fi_sections_text_diagnostics: {len(all_diag_rows):,} rows")

    total_elapsed = time.time() - t_start
    print(f"  total wall time: {total_elapsed:.1f}s")

    return jsonl_count

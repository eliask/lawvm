"""Export fi_actors.parquet -- ActorMention projection for Finland.

Produces fi_actors.parquet (and fi_actors.jsonl fallback) by running
extract_actor_mentions over each statute in the corpus.

This module is called by export_parquet.main() when --include-actors is passed,
and also available as a standalone entry point.

Schema: per ACTOR_MENTION_EXTRACTION.md §Projection export.

Usage (standalone):
    python -m lawvm.tools.export_fi_actors --data-dir .tmp/projections

Called from export_parquet:
    export_fi_actors(corpus, data_dir=..., use_parquet=True)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.actor_mention import actor_mention_to_row


def _load_corpus_store() -> Any:
    """Load the Finland consolidated corpus store for XML acquisition."""
    from lawvm.finland.corpus import get_corpus_store
    return get_corpus_store()


def _get_statute_xml(statute_id: str, store: Any) -> Optional[bytes]:
    """Get XML bytes for a statute from the corpus store.

    Returns None if the statute is not available.
    """
    try:
        return store.read_oracle(statute_id)
    except Exception:
        return None


def _project_actors_for_statute(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project ActorMention rows for one statute.

    Returns (mention_rows, diagnostic_rows).
    """
    from lawvm.finland.actor_mention_extractor import extract_actor_mentions

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    result = extract_actor_mentions(xml_bytes, statute_id)

    mention_rows: List[Dict[str, Any]] = []
    for mention in result.mentions:
        row = actor_mention_to_row(mention)
        row["source_statute_id"] = statute_id
        mention_rows.append(row)

    # Emit diagnostics (rejected + ambiguous + lifecycle) for audit trail
    diag_rows: List[Dict[str, Any]] = []

    for rej in result.rejected:
        diag_rows.append({
            "statute_id": statute_id,
            "kind": "rejected_actor_candidate",
            "rule_id": rej.rule_id,
            "phase": rej.phase,
            "reason": rej.reason,
            "matched_text": rej.matched_text,
            "blocking": rej.blocking,
        })

    for af in result.ambiguous_findings:
        diag_rows.append({
            "statute_id": statute_id,
            "kind": "ambiguous_actor_mention",
            "rule_id": af.rule_id,
            "phase": af.phase,
            "actor_phrase": af.actor_phrase,
            "candidate_ids": list(af.candidate_canonical_ids),
            "reason": af.reason,
            "blocking": af.blocking,
        })

    for obs in result.lifecycle_observations:
        diag_rows.append({
            "statute_id": statute_id,
            "kind": "lifecycle_actor_observation",
            "rule_id": obs.rule_id,
            "phase": obs.phase,
            "actor_phrase": obs.actor_phrase,
            "predecessor_id": obs.predecessor_id,
            "successor_id": obs.successor_id,
            "lifecycle_date": obs.lifecycle_date.isoformat(),
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
            pa.field("actor_phrase", pa.string()),
            pa.field("actor_canonical_id", pa.string()),
            pa.field("actor_canonical_show_as", pa.string()),
            pa.field("modal_kind", pa.string()),
            pa.field("resolution_confidence", pa.string()),
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


def export_fi_actors(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
) -> int:
    """Export fi_actors.parquet projection for a corpus of Finnish statutes.

    Args:
        corpus:      List of (amendment_count, statute_id) tuples.
        data_dir:    Output directory. fi_actors.parquet written here.
        use_parquet: Write Parquet if pyarrow available (also writes JSONL).
        limit:       Process only first N statutes (for testing).

    Returns:
        Number of ActorMention rows written.
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
        mention_rows, diag_rows = _project_actors_for_statute(statute_id, store)
        all_mention_rows.extend(mention_rows)
        all_diag_rows.extend(diag_rows)

        if i % 50 == 0 or i == total:
            elapsed = time.time() - t0
            print(f"  [{i}/{total}] actors: {len(all_mention_rows):,} total ({elapsed:.1f}s last)")

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Always write JSONL (DuckDB can read it)
    jsonl_count = _write_jsonl(out / "fi_actors.jsonl", all_mention_rows)

    if use_parquet:
        ok = _try_write_parquet(out / "fi_actors.parquet", all_mention_rows)
        if ok:
            print(f"  fi_actors: {jsonl_count:,} rows (Parquet + JSONL)")
        else:
            print(f"  fi_actors: {jsonl_count:,} rows (JSONL only; pyarrow not installed)")
    else:
        print(f"  fi_actors: {jsonl_count:,} rows (JSONL)")

    # Write diagnostics for audit trail
    if all_diag_rows:
        _write_jsonl(out / "fi_actors_diagnostics.jsonl", all_diag_rows)

    return jsonl_count

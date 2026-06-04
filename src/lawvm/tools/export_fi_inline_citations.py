"""Export fi_inline_citations.parquet — InlineCitation projection for Finland.

Produces fi_inline_citations.parquet (and fi_inline_citations.jsonl fallback)
by running extract_inline_citations over:
  1. Each statute in the finlex.farchive corpus (doc_kind='statute')
  2. Each HE in the fi_government_proposal.farchive corpus (doc_kind='he')

This module is called by export_parquet.main() when --include-inline-citations
is passed, and is also available as a standalone entry point.

Schema: per INLINE_CITATION_SWEEP.md §Projection + CLI.

Usage (standalone):
    python -m lawvm.tools.export_fi_inline_citations --data-dir .tmp/projections

Called from export_parquet:
    export_fi_inline_citations(corpus, data_dir=..., use_parquet=True,
                               he_farchive_path=..., limit=...)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.inline_citation import inline_citation_to_row


def _load_corpus_store() -> Any:
    """Load the Finland consolidated corpus store for statute XML acquisition."""
    from lawvm.finland.corpus import get_corpus_store
    return get_corpus_store()


def _get_statute_xml(statute_id: str, store: Any) -> Optional[bytes]:
    """Get XML bytes for a statute from the corpus store. Returns None if unavailable."""
    return store.read_oracle(statute_id)


def _project_inline_citations_for_statute(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project InlineCitation rows for one enacted statute.

    Returns (citation_rows, diagnostic_rows).
    """
    from lawvm.finland.inline_citation_extractor import extract_inline_citations

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    extraction = extract_inline_citations(
        xml_bytes,
        doc_id=statute_id,
        doc_kind="statute",
        source_span_file=None,
    )

    citation_rows = [inline_citation_to_row(c) for c in extraction.citations]

    diag_rows: List[Dict[str, Any]] = []
    for pm in extraction.pattern_matches:
        diag_rows.append({
            "doc_id": statute_id,
            "doc_kind": "statute",
            "kind": "inline_citation_pattern_match",
            "rule_id": pm.rule_id,
            "phase": pm.phase,
            "reason": pm.reason,
            "raw_text": pm.raw_text[:200],
            "kind_attempted": pm.kind_attempted,
            "blocking": pm.blocking,
        })

    return citation_rows, diag_rows


def _project_inline_citations_for_he(
    xml_bytes: bytes,
    he_id: str,
    locator: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project InlineCitation rows for one HE body.

    Args:
        xml_bytes: Raw XML bytes.
        he_id:     Human-readable HE ID (e.g. 'HE 116/2024'), used as doc_id.
        locator:   Farchive locator for source_span_file provenance.

    Returns (citation_rows, diagnostic_rows).
    """
    from lawvm.finland.inline_citation_extractor import extract_inline_citations

    # Use the short form "116/2024" as doc_id (matches corpus convention)
    # He IDs like "HE 116/2024" → "116/2024"
    short_id = he_id.replace("HE ", "").strip() if he_id.startswith("HE ") else he_id

    extraction = extract_inline_citations(
        xml_bytes,
        doc_id=short_id,
        doc_kind="he",
        source_span_file=locator,
    )

    citation_rows = [inline_citation_to_row(c) for c in extraction.citations]

    diag_rows: List[Dict[str, Any]] = []
    for pm in extraction.pattern_matches:
        diag_rows.append({
            "doc_id": short_id,
            "doc_kind": "he",
            "kind": "inline_citation_pattern_match",
            "rule_id": pm.rule_id,
            "phase": pm.phase,
            "reason": pm.reason,
            "raw_text": pm.raw_text[:200],
            "kind_attempted": pm.kind_attempted,
            "blocking": pm.blocking,
        })

    return citation_rows, diag_rows


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    """Write rows as JSONL; return count written."""
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
        schema = pa.schema([
            pa.field("source_doc_id", pa.string()),
            pa.field("source_doc_kind", pa.string()),
            pa.field("source_provision_ref", pa.string()),
            pa.field("kind", pa.string()),
            pa.field("canonical_id", pa.string()),
            pa.field("raw_text", pa.string()),
            pa.field("case_year", pa.int32()),
            pa.field("case_number", pa.int32()),
            pa.field("context", pa.string()),
            pa.field("source_span_file", pa.string()),
            pa.field("source_span_byte_offset", pa.int64()),
            pa.field("source_span_byte_len", pa.int64()),
        ])
        table = pa.table({col: [] for col in schema.names}, schema=schema)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(path), compression="zstd")
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, str(path), compression="zstd")
    return True


def export_fi_inline_citations(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    he_farchive_path: Optional[str] = None,
    limit: Optional[int] = None,
) -> int:
    """Export fi_inline_citations.parquet projection.

    Runs over:
      1. Enacted statutes from finlex.farchive (corpus arg).
      2. HE bodies from fi_government_proposal.farchive (he_farchive_path).

    Args:
        corpus:           List of (amendment_count, statute_id) tuples (enacted statutes).
        data_dir:         Output directory. fi_inline_citations.parquet written here.
        use_parquet:      Write Parquet if pyarrow available (always writes JSONL).
        he_farchive_path: Path to fi_government_proposal.farchive. If None, skip HE extraction.
        limit:            Process only first N statutes (for testing).

    Returns:
        Total number of InlineCitation rows written (statutes + HEs combined).
    """
    store = _load_corpus_store()

    if limit:
        corpus = corpus[:limit]

    total_statutes = len(corpus)
    all_citation_rows: List[Dict[str, Any]] = []
    all_diag_rows: List[Dict[str, Any]] = []

    # --- Phase 1: Enacted statutes ---
    print(f"  inline_citations: processing {total_statutes:,} statutes...")
    for i, (_, statute_id) in enumerate(corpus, 1):
        citation_rows, diag_rows = _project_inline_citations_for_statute(statute_id, store)
        all_citation_rows.extend(citation_rows)
        all_diag_rows.extend(diag_rows)

        if i % 100 == 0 or i == total_statutes:
            print(
                f"  [{i}/{total_statutes}] inline_citations (statutes): "
                f"{len(all_citation_rows):,} total"
            )

    # --- Phase 2: HE bodies ---
    if he_farchive_path and Path(he_farchive_path).exists():
        he_count = _project_from_he_farchive(
            he_farchive_path=he_farchive_path,
            all_citation_rows=all_citation_rows,
            all_diag_rows=all_diag_rows,
            limit=limit,
        )
        print(
            f"  inline_citations: {he_count:,} HE citations added; "
            f"{len(all_citation_rows):,} total"
        )
    else:
        if he_farchive_path:
            print(
                f"  inline_citations: HE farchive not found at {he_farchive_path!r}; "
                f"skipping HE extraction",
                file=sys.stderr,
            )

    # --- Write output ---
    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    jsonl_count = _write_jsonl(out / "fi_inline_citations.jsonl", all_citation_rows)

    if use_parquet:
        ok = _try_write_parquet(out / "fi_inline_citations.parquet", all_citation_rows)
        if ok:
            print(f"  fi_inline_citations: {jsonl_count:,} rows (Parquet + JSONL)")
        else:
            print(
                f"  fi_inline_citations: {jsonl_count:,} rows "
                f"(JSONL only; pyarrow not installed)"
            )
    else:
        print(f"  fi_inline_citations: {jsonl_count:,} rows (JSONL)")

    if all_diag_rows:
        _write_jsonl(out / "fi_inline_citations_diagnostics.jsonl", all_diag_rows)

    return jsonl_count


def _project_from_he_farchive(
    he_farchive_path: str,
    all_citation_rows: List[Dict[str, Any]],
    all_diag_rows: List[Dict[str, Any]],
    limit: Optional[int],
) -> int:
    """Extract inline citations from HE farchive. Returns count of HE citation rows added."""
    try:
        from farchive import Farchive  # ty: ignore[unresolved-import]
    except ImportError:
        print(
            "  inline_citations: farchive not installed; skipping HE extraction",
            file=sys.stderr,
        )
        return 0

    from lawvm.finland.he_acquisition import (
        HEStructuralTier,
        classify_structural_tier,
        _AKN_NS,
        _extract_he_id,
    )
    try:
        from lxml import etree
    except ImportError:
        print(
            "  inline_citations: lxml not installed; skipping HE extraction",
            file=sys.stderr,
        )
        return 0

    farchive = Farchive(he_farchive_path)
    prefix = "akn/fi/doc/government-proposal/"
    lang_suffix = "/fin@/main.xml"  # Finnish language suffix (not "fi@")

    done_he = 0
    he_citation_count = 0

    try:
        all_locators = farchive.locators()
        for locator in all_locators:
            if limit is not None and done_he >= limit:
                break
            if not locator.startswith(prefix):
                continue
            if not locator.endswith(lang_suffix):
                continue

            xml_bytes = farchive.get(locator)
            if xml_bytes is None:
                continue

            # Parse HE XML to check if it's FULL_AKN (has body content)
            xml_root = etree.fromstring(xml_bytes)
            tier = classify_structural_tier(xml_root)
            if tier != HEStructuralTier.FULL_AKN:
                continue

            he_id = _extract_he_id(xml_root)
            if not he_id:
                continue

            # Use stdlib ET for compatibility with the extractor
            import xml.etree.ElementTree as ET
            root_et = ET.fromstring(xml_bytes)
            he_id_short = he_id.replace("HE ", "").strip() if he_id.startswith("HE ") else he_id

            citation_rows, diag_rows = _project_inline_citations_for_he(
                xml_bytes=ET.tostring(root_et),
                he_id=he_id,
                locator=locator,
            )
            all_citation_rows.extend(citation_rows)
            all_diag_rows.extend(diag_rows)
            he_citation_count += len(citation_rows)
            done_he += 1

            if done_he % 200 == 0:
                print(
                    f"  [{done_he}] inline_citations (HEs): "
                    f"{he_citation_count:,} HE citations so far"
                )
    finally:
        farchive.close()

    return he_citation_count

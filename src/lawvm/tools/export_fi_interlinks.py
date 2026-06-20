"""Export lawvm_interlinks — neutral citation/interlink projection.

This exporter is an adapter layer over existing Finland citation primitives:

* ReferenceMention       -> XML/metadata statute references
* PreparatoryReference   -> preparatory-history references
* InlineCitation         -> body-prose references

Recognition and resolution remain in LawVM frontend/core code.  Viewers should
consume this projection and must not parse legal prose themselves.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.interlinks import (
    INTERLINK_ROW_COLUMNS,
    legal_interlink_to_row,
)
from lawvm.finland.interlinks import (
    fi_interlink_from_inline_citation,
    fi_interlink_from_preparatory_reference,
    fi_interlink_from_reference_mention,
)
from lawvm.finland.legal_surface.overlay_projection import (
    OVERLAY_ROW_COLUMNS,
    graph_to_overlay_rows,
)


def _load_corpus_store() -> Any:
    from lawvm.finland.corpus import get_corpus_store

    return get_corpus_store()


def _get_statute_xml(statute_id: str, store: Any) -> Optional[bytes]:
    xml = store.read_oracle(statute_id)
    if xml is not None:
        return xml
    left, sep, right = statute_id.partition("/")
    if sep and len(right) == 4:
        return store.read_oracle(f"{right}/{left}")
    return None


def _stable_interlink_id(family: str, statute_id: str, index: int) -> str:
    safe_statute_id = statute_id.replace("/", "_")
    return f"fi.{family}:{safe_statute_id}:{index}"


def _project_interlinks_for_statute(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project neutral interlink rows for one Finnish statute."""
    from lawvm.finland.references.inline_citation_extractor import (
        extract_inline_citations,
    )
    from lawvm.finland.references.preparatory_reference_extractor import (
        extract_preparatory_refs,
    )
    from lawvm.finland.ref_mention_extractor import extract_all_reference_mentions

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    rows: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []

    ref_result = extract_all_reference_mentions(xml_bytes, statute_id)
    for index, mention in enumerate(ref_result.mentions):
        link = fi_interlink_from_reference_mention(
            mention,
            interlink_id=_stable_interlink_id("refs", statute_id, index),
        )
        rows.append(legal_interlink_to_row(link))
    for diag in ref_result.diagnostics:
        diagnostics.append({
            "statute_id": statute_id,
            "family": "fi_refs",
            "rule_id": diag.rule_id,
            "phase": diag.phase,
            "reason": diag.reason,
            "blocking": diag.blocking,
        })

    prep_result = extract_preparatory_refs(xml_bytes, statute_id)
    for index, ref in enumerate(prep_result.refs):
        link = fi_interlink_from_preparatory_reference(
            ref,
            interlink_id=_stable_interlink_id("preparatory_refs", statute_id, index),
        )
        rows.append(legal_interlink_to_row(link))
    for rej in prep_result.rejected:
        diagnostics.append({
            "statute_id": statute_id,
            "family": "fi_preparatory_refs",
            "kind": "rejected_preparatory_candidate",
            "rule_id": rej.rule_id,
            "phase": rej.phase,
            "reason": rej.reason,
            "raw_text": rej.raw_text[:200],
            "blocking": rej.blocking,
        })
    for obs in prep_result.lifecycle_observations:
        diagnostics.append({
            "statute_id": statute_id,
            "family": "fi_preparatory_refs",
            "kind": "committee_lifecycle_observation",
            "rule_id": obs.rule_id,
            "phase": obs.phase,
            "committee_abbrev": obs.committee_abbrev,
            "canonical_id": obs.canonical_id,
            "lifecycle_event": obs.lifecycle_event,
        })

    inline_result = extract_inline_citations(
        xml_bytes,
        doc_id=statute_id,
        doc_kind="statute",
        source_span_file=None,
    )
    for index, citation in enumerate(inline_result.citations):
        link = fi_interlink_from_inline_citation(
            citation,
            interlink_id=_stable_interlink_id("inline_citations", statute_id, index),
        )
        rows.append(legal_interlink_to_row(link))
    for match in inline_result.pattern_matches:
        diagnostics.append({
            "statute_id": statute_id,
            "family": "fi_inline_citations",
            "kind": "inline_citation_pattern_match",
            "rule_id": match.rule_id,
            "phase": match.phase,
            "reason": match.reason,
            "raw_text": match.raw_text[:200],
            "kind_attempted": match.kind_attempted,
            "blocking": match.blocking,
        })

    return rows, diagnostics


def _project_overlays_for_statute(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project FULL Legal Surface Graph overlay rows for one Finnish statute.

    Builds the whole-statute Legal Surface Graph (all 9 lenses + edges) and
    projects every renderable surface node into a ``lawvm_surface_overlays`` row.
    Returns ``(overlay_rows, [])`` — no diagnostics channel (the graph build's own
    diagnostics live on the graph; the overlay projection adds none). Returns
    ``([], [])`` when the statute XML is absent (same fail-by-absence as the
    interlink projector).

    The ``rendered_*`` columns are NULL here: the v0 whole-body graph anchor
    carries no effective_date / segment_index / address, so no
    ``OverlayRenderedSpanContext`` is supplied — matching the interlink export,
    which likewise emits null ``rendered_*``. A PIT-aware caller can supply the
    context to populate them.
    """
    from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    graph = build_legal_surface_graph(xml_bytes, statute_id)
    return graph_to_overlay_rows(graph), []


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def _attach_compile_metadata(table: Any, compile_metadata: Any) -> Any:
    if compile_metadata is None:
        raise ValueError(
            "export_fi_interlinks: CompileMetadata is required for v3 substrate-locked "
            "persistence. Construct via build_default_compile_metadata() or explicitly."
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
    empty_schema_columns: Tuple[str, ...] = INTERLINK_ROW_COLUMNS,
) -> bool:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False

    if rows:
        table = pa.Table.from_pylist(rows)
    else:
        schema = pa.schema([pa.field(col, pa.string()) for col in empty_schema_columns])
        table = pa.table({col: [] for col in schema.names}, schema=schema)
    table = _attach_compile_metadata(table, compile_metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(path), compression="zstd")
    return True


def export_fi_interlinks(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Export neutral ``lawvm_interlinks`` rows for Finnish citation surfaces."""
    store = _load_corpus_store()
    if limit:
        corpus = corpus[:limit]

    from lawvm.tools._parallel_corpus import project_corpus_parallel

    statute_ids = [sid for _, sid in corpus]
    all_rows, all_diagnostics = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=(__name__, "_project_interlinks_for_statute"),
        serial_projector=_project_interlinks_for_statute,
        store=store,
        workers=workers,
    )
    print(
        f"  interlinks: {len(all_rows):,} rows over "
        f"{len(statute_ids):,} statutes"
    )

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl_count = _write_jsonl(out / "lawvm_interlinks.jsonl", all_rows)

    if use_parquet:
        ok = _try_write_parquet(out / "lawvm_interlinks.parquet", all_rows, compile_metadata)
        if ok:
            print(f"  lawvm_interlinks: {jsonl_count:,} rows (Parquet + JSONL)")
        else:
            print(
                f"  lawvm_interlinks: {jsonl_count:,} rows "
                f"(JSONL only; pyarrow not installed)"
            )
    else:
        print(f"  lawvm_interlinks: {jsonl_count:,} rows (JSONL)")

    if all_diagnostics:
        _write_jsonl(out / "lawvm_interlinks_diagnostics.jsonl", all_diagnostics)

    # ── lawvm_surface_overlays: the FULL Legal Surface Graph projection ───────
    # Emitted in the SAME export run as the interlinks. One row per renderable
    # surface node (defined terms, term-uses, temporal markers, delegation /
    # sanction / exception frames, actor/modal frames, references). Placeable by
    # the SAME rendered-span columns the interlink rows carry.
    overlay_rows, _ = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=(__name__, "_project_overlays_for_statute"),
        serial_projector=_project_overlays_for_statute,
        store=store,
        workers=workers,
    )
    print(
        f"  surface_overlays: {len(overlay_rows):,} rows over "
        f"{len(statute_ids):,} statutes"
    )
    overlay_jsonl_count = _write_jsonl(out / "lawvm_surface_overlays.jsonl", overlay_rows)
    if use_parquet:
        ok = _try_write_parquet(
            out / "lawvm_surface_overlays.parquet",
            overlay_rows,
            compile_metadata,
            empty_schema_columns=OVERLAY_ROW_COLUMNS,
        )
        if ok:
            print(f"  lawvm_surface_overlays: {overlay_jsonl_count:,} rows (Parquet + JSONL)")
        else:
            print(
                f"  lawvm_surface_overlays: {overlay_jsonl_count:,} rows "
                f"(JSONL only; pyarrow not installed)"
            )
    else:
        print(f"  lawvm_surface_overlays: {overlay_jsonl_count:,} rows (JSONL)")

    return jsonl_count


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Export neutral LawVM interlinks")
    parser.add_argument("--data-dir", default=".tmp/projections")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--no-parquet", action="store_true")
    args = parser.parse_args()

    from lawvm.tools.export_parquet import _load_corpus

    export_fi_interlinks(
        _load_corpus("all"),
        data_dir=args.data_dir,
        use_parquet=not args.no_parquet,
        limit=args.limit,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()

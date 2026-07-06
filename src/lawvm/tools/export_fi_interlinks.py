"""Export lawvm_interlinks — neutral citation/interlink projection.

This exporter is an adapter layer over existing Finland citation primitives:

* ReferenceMention       -> XML/metadata statute references
* PreparatoryReference   -> preparatory-history references
* InlineCitation         -> body-prose references

Recognition and resolution remain in LawVM frontend/core code.  Viewers should
consume this projection and must not parse legal prose themselves.
"""
from __future__ import annotations

import dataclasses
import functools
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.filter_result import FilterResult
from lawvm.core.interlinks import (
    INTERLINK_ROW_COLUMNS,
    legal_interlink_to_row,
)
from lawvm.core.stage_result import (
    NEUTRAL_AUTHORITY,
    AuthoritySurface,
    CoverageCertificate,
    PartitionResult,
    Residual,
)
from lawvm.finland.interlinks import (
    fi_interlink_from_inline_citation,
    fi_interlink_from_preparatory_reference,
    fi_interlink_from_reference_mention,
    fi_work_ref_from_canonical_id,
)
from lawvm.finland.legal_surface.overlay_projection import (
    OVERLAY_ROW_COLUMNS,
    graph_to_overlay_rows,
)


@dataclasses.dataclass(frozen=True)
class FiProjectionResult(PartitionResult[Dict[str, Any]]):
    """Conserving carrier for a Finnish viewer/overlay projection (WAIST #10).

    Composes the canonical :class:`PartitionResult` over the emitted projection
    rows (``accepted``), the previously-discarded extractor diagnostics + any
    dropped universe member as typed :class:`Residual` (``residuals``), and a
    :class:`CoverageCertificate` partition account (``coverage``).

    It additionally carries an :class:`AuthoritySurface`, fixed at
    :data:`NEUTRAL_AUTHORITY`: a viewer/projection row is NOT a legal-state fact
    and carries NO replay authority (Pro §8 / §13.9). This is the one waist where
    ``NEUTRAL_AUTHORITY`` is the CORRECT, load-bearing value — the firewall that
    keeps a projection from being mistaken for a legal conclusion.
    """

    authority: AuthoritySurface = NEUTRAL_AUTHORITY

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.authority, AuthoritySurface):
            raise ValueError("FiProjectionResult.authority must be an AuthoritySurface")

    @property
    def rows(self) -> Tuple[Dict[str, Any], ...]:
        """Convenience alias for the accepted (emitted) projection rows."""
        return self.accepted


def _projection_residual_from_diagnostic(
    diagnostic: Dict[str, Any],
    *,
    statute_id: str,
) -> Residual:
    """Map one currently-discarded extractor diagnostic onto a typed Residual."""
    family = str(diagnostic.get("family", ""))
    rule_id = str(diagnostic.get("rule_id", "")) or "fi_projection_diagnostic"
    reason = str(diagnostic.get("reason", "") or diagnostic.get("kind", ""))
    parts = [part for part in (family, rule_id, reason) if part]
    return Residual(
        kind="projection_residual",
        reason=": ".join(parts) if parts else rule_id,
        scope=f"{family or 'fi_projection'}:{statute_id}",
        source_unit_id=statute_id,
        text=str(diagnostic.get("raw_text", "")),
        blocking=bool(diagnostic.get("blocking", False)),
    )


def _build_projection_coverage(
    *,
    unit: str,
    rows: List[Dict[str, Any]],
    residuals: Tuple[Residual, ...],
    dropped_universe_members: int,
) -> CoverageCertificate:
    """Build the four-class coverage account for a projection.

    ``violation`` counts silently-dropped universe members (the rank-21
    silent-drop class): a statute the projector was asked to project but which
    yielded neither an emitted row nor a recorded residual.
    """
    return CoverageCertificate(
        unit=unit,
        total=len(rows) + len(residuals) + dropped_universe_members,
        owned=len(rows),
        residual=len(residuals),
        violation=dropped_universe_members,
        totality_claimed=True,
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


@functools.lru_cache(maxsize=1)
def _cached_reference_registries() -> Tuple[Any, Any]:
    """Build (statute_name, eu_nickname) registries ONCE per process.

    The interlink projector runs per-statute (and inside parallel workers). The
    statute-name registry is a single cheap file read of the persisted full-corpus
    artifact (:func:`references.resolve.build_default_registries`), so we cache it
    at module level — each worker builds it once, never per statute. This is a
    pure READ-side artifact (the citation graph), never a replay input.
    """
    from lawvm.finland.references.resolve import build_default_registries

    return build_default_registries()


def _candidate_work_ids_from_resolution(resolved: Any) -> Tuple[str, ...]:
    """Map an AMBIGUOUS-but-resolvable resolution to a SMALL discrete work-id set.

    A ``references.resolve.ResolvedReference`` whose ``resolution_status`` is
    AMBIGUOUS carries the FULL discrete candidate set the registry returned but
    refused to pick among (e.g. several versions of a multi-version by-name act,
    or several CELEX for one EU nickname). We surface that set — normalized to the
    same canonical work-id form the resolved single-target rows use
    (``fi:normative_act:NNN/YYYY``, ``celex:…`` passed through) — as the published
    ``candidate_work_ids``. Every other status (RESOLVED single, STATUTE_ONLY miss,
    OPEN/BROKEN/UNCHANGED) carries no candidate set (empty tuple), exactly as
    before: only the genuinely one-of-K case is surfaced, never a laundered pick.
    """
    from lawvm.finland.references.resolve import ResolutionStatus

    if resolved.resolution_status is not ResolutionStatus.AMBIGUOUS:
        return ()
    work_ids: List[str] = []
    for cid in resolved.candidates:
        cid = str(cid or "")
        if not cid:
            continue
        # EU nickname candidates already arrive as ``celex:<CELEX>`` work ids and
        # any other already-namespaced id (``prefix:body``) is passed through
        # untouched. A bare Finnish statute id (``NNN/YYYY``) is normalized to its
        # canonical work id so a candidate matches the ``target_work_id`` form of a
        # resolved row.
        if ":" in cid:
            work_ids.append(cid)
            continue
        work = fi_work_ref_from_canonical_id(cid)
        work_ids.append(work.canonical_id if work is not None else cid)
    return tuple(work_ids)


def _project_interlinks_for_statute(
    statute_id: str,
    store: Any,
) -> FiProjectionResult:
    """Project neutral interlink rows for one Finnish statute as a conserving partition.

    Returns a :class:`FiProjectionResult`: ``accepted`` are the emitted interlink
    rows (byte-identical to the previous bare-row return), ``residuals`` carry one
    typed :class:`Residual` per previously-discarded extractor diagnostic (and one
    blocking residual when the statute XML is absent — a drop that used to be
    silent), ``coverage`` is the four-class account, and ``authority`` is the
    load-bearing :data:`NEUTRAL_AUTHORITY` (a projection row is not a legal-state
    fact).
    """
    from lawvm.finland.references.inline_citation_extractor import (
        extract_inline_citations,
    )
    from lawvm.finland.references.preparatory_reference_extractor import (
        extract_preparatory_refs,
    )
    from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions
    from lawvm.finland.references.resolve import resolve_mentions

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        # A universe member the projector was asked to project but could not:
        # record it as a blocking residual instead of dropping it silently, and
        # count it as a coverage violation (the rank-21 silent-drop class).
        absent = Residual(
            kind="projection_residual",
            reason="fi_interlinks: statute_xml_absent: no oracle/source XML for statute",
            scope=f"fi_interlinks:{statute_id}",
            source_unit_id=statute_id,
            blocking=True,
        )
        return FiProjectionResult(
            FilterResult(),
            residuals=(absent,),
            coverage=_build_projection_coverage(
                unit="projection_rows",
                rows=[],
                residuals=(absent,),
                dropped_universe_members=1,
            ),
        )

    rows: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []

    ref_result = extract_all_reference_mentions(xml_bytes, statute_id)
    # RESOLUTION (read/publish only): project each raw placeholder mention through
    # the reference-resolution projection so the disambiguation the resolver
    # already computes is SURFACED in the published citation graph. A ``fi-name:``
    # or ``eu-nickname:`` placeholder that resolves to a single act is rewritten to
    # that act's real id (a resolved target on the row); one that resolves to a
    # SMALL discrete candidate set (multi-version by-name, multi-CELEX nickname)
    # is carried AMBIGUOUS-but-resolvable with ``candidate_work_ids`` populated
    # rather than dropped. This is side-effect-free on replay (the resolver is a
    # pure downstream projection) and honours fail-loud (never picks one of many).
    statute_registry, eu_registry = _cached_reference_registries()
    resolutions = resolve_mentions(
        list(ref_result.mentions),
        statute_registry=statute_registry,
        eu_registry=eu_registry,
    )
    for index, resolved in enumerate(resolutions):
        candidate_work_ids = _candidate_work_ids_from_resolution(resolved)
        link = fi_interlink_from_reference_mention(
            resolved.mention,
            interlink_id=_stable_interlink_id("refs", statute_id, index),
            candidate_work_ids=candidate_work_ids,
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

    residuals = tuple(
        _projection_residual_from_diagnostic(diagnostic, statute_id=statute_id)
        for diagnostic in diagnostics
    )
    return FiProjectionResult(
        FilterResult(accepted_items=tuple(rows)),
        residuals=residuals,
        coverage=_build_projection_coverage(
            unit="projection_rows",
            rows=rows,
            residuals=residuals,
            dropped_universe_members=0,
        ),
    )


def _project_overlays_for_statute(
    statute_id: str,
    store: Any,
) -> FiProjectionResult:
    """Project FULL Legal Surface Graph overlay rows for one Finnish statute.

    Builds the whole-statute Legal Surface Graph (all 9 lenses + edges) and
    projects every renderable surface node into a ``lawvm_surface_overlays`` row.
    Returns a :class:`FiProjectionResult` whose ``accepted`` are the overlay rows
    (byte-identical to the previous bare-row return) — the overlay projection has
    no diagnostics channel of its own (the graph build's diagnostics live on the
    graph), so ``residuals`` is empty UNLESS the statute XML is absent, in which
    case the dropped universe member is recorded as a blocking residual + coverage
    violation instead of being dropped silently. ``authority`` is
    :data:`NEUTRAL_AUTHORITY`.

    The ``rendered_*`` columns are NULL here: the v0 whole-body graph anchor
    carries no effective_date / segment_index / address, so no
    ``OverlayRenderedSpanContext`` is supplied — matching the interlink export,
    which likewise emits null ``rendered_*``. A PIT-aware caller can supply the
    context to populate them.
    """
    from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        absent = Residual(
            kind="projection_residual",
            reason="fi_surface_overlays: statute_xml_absent: no oracle/source XML for statute",
            scope=f"fi_surface_overlays:{statute_id}",
            source_unit_id=statute_id,
            blocking=True,
        )
        return FiProjectionResult(
            FilterResult(),
            residuals=(absent,),
            coverage=_build_projection_coverage(
                unit="overlay_rows",
                rows=[],
                residuals=(absent,),
                dropped_universe_members=1,
            ),
        )

    graph = build_legal_surface_graph(xml_bytes, statute_id)
    rows = graph_to_overlay_rows(graph)
    return FiProjectionResult(
        FilterResult(accepted_items=tuple(rows)),
        coverage=_build_projection_coverage(
            unit="overlay_rows",
            rows=rows,
            residuals=(),
            dropped_universe_members=0,
        ),
    )


# ── Parallel-harness adapters ─────────────────────────────────────────────────
# ``_parallel_corpus.project_corpus_parallel`` (shared by every export_fi_* tool)
# fixes the per-statute projector return to ``(rows, diag_rows)`` of plain dicts.
# These adapters drive the carrier-returning projectors and flatten the typed
# ``residuals`` lane onto the diagnostics list, tagging each with the fields the
# entrypoint needs to reassemble a corpus-level CoverageCertificate (and the
# blocking flag the console branch reads). The accepted rows are returned
# unchanged → byte-identity is preserved.

_PROJECTION_RESIDUAL_DIAG_KIND = "fi_projection_residual"


def _residual_to_diag_row(residual: Residual) -> Dict[str, Any]:
    return {
        "kind": _PROJECTION_RESIDUAL_DIAG_KIND,
        "residual_kind": residual.kind,
        "reason": residual.reason,
        "scope": residual.scope,
        "statute_id": residual.source_unit_id,
        "blocking": residual.blocking,
        # An xml-absent residual is a dropped universe member (the coverage
        # ``violation`` class); any other diagnostic is a ``residual``. The
        # entrypoint partitions the corpus account on this marker.
        "is_universe_drop": "statute_xml_absent" in residual.reason,
    }


def _project_interlinks_rows_and_diags(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parallel-harness adapter: ``(rows, residual_diag_rows)`` for interlinks."""
    projection = _project_interlinks_for_statute(statute_id, store)
    return (
        list(projection.rows),
        [_residual_to_diag_row(residual) for residual in projection.residuals],
    )


def _project_overlays_rows_and_diags(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Parallel-harness adapter: ``(rows, residual_diag_rows)`` for overlays."""
    projection = _project_overlays_for_statute(statute_id, store)
    return (
        list(projection.rows),
        [_residual_to_diag_row(residual) for residual in projection.residuals],
    )


def _corpus_projection_coverage(
    *,
    unit: str,
    rows: List[Dict[str, Any]],
    residual_diag_rows: List[Dict[str, Any]],
) -> CoverageCertificate:
    """Reassemble a corpus-level CoverageCertificate from the flattened lanes."""
    drops = sum(1 for d in residual_diag_rows if d.get("is_universe_drop"))
    residual_count = len(residual_diag_rows) - drops
    return CoverageCertificate(
        unit=unit,
        total=len(rows) + residual_count + drops,
        owned=len(rows),
        residual=residual_count,
        violation=drops,
        totality_claimed=True,
    )


def _emit_projection_residual_branch(
    *,
    label: str,
    residual_diag_rows: List[Dict[str, Any]],
) -> int:
    """Read the residual lane and fail-loud on blocking residue (export console).

    Mirrors the ``interlink_targets.project_fi_interlinks_for_transition_graph``
    console convention. Returns the number of blocking residuals surfaced.
    """
    blocking = [d for d in residual_diag_rows if d.get("blocking")]
    if blocking:
        sample = "; ".join(str(d.get("reason", "")) for d in blocking[:5])
        print(
            f"[export] WARNING: fi {label} carry {len(blocking)} blocking "
            f"projection residual(s) that are NOT emitted as rows "
            f"(dropped universe members / blocking diagnostics): {sample}",
            file=sys.stderr,
            flush=True,
        )
    return len(blocking)


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


def _coverage_leaf(coverage: CoverageCertificate) -> Dict[str, Any]:
    """The honest per-table projection-coverage leaf (rank-21 shape)."""
    return {
        "universe_kind": "corpus_statute_set",
        "address_source": "export_fi_interlinks.corpus",
        "unit": coverage.unit,
        "total": coverage.total,
        "row_count": coverage.owned,
        "residual_count": coverage.residual,
        "omitted_row_count": coverage.violation,
        "is_partition": coverage.is_partition(),
        "is_clean": coverage.is_clean,
    }


def _write_coverage_leaf(path: Path, coverage: CoverageCertificate) -> None:
    """Write the coverage leaf as a JSON sidecar so the artifact carries an
    honest per-table account even when pyarrow is unavailable. A
    ``violation > 0`` (silent drop) is then a recorded, checkable fact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_coverage_leaf(coverage), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _try_write_parquet(
    path: Path,
    rows: List[Dict[str, Any]],
    compile_metadata: Any = None,
    empty_schema_columns: Tuple[str, ...] = INTERLINK_ROW_COLUMNS,
    *,
    coverage: Optional[CoverageCertificate] = None,
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
    if coverage is not None:
        existing = table.schema.metadata or {}
        meta = dict(existing)
        meta[b"lawvm.projection_coverage"] = json.dumps(
            _coverage_leaf(coverage), ensure_ascii=False, sort_keys=True
        ).encode()
        table = table.replace_schema_metadata(meta)
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
    # The carrier-returning projectors are driven through the parallel-harness
    # adapters, which flatten the typed ``residuals`` lane onto the diagnostics
    # list (preserving the byte-identical accepted rows).
    all_rows, all_diagnostics = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=(__name__, "_project_interlinks_rows_and_diags"),
        serial_projector=_project_interlinks_rows_and_diags,
        store=store,
        workers=workers,
    )
    print(
        f"  interlinks: {len(all_rows):,} rows over "
        f"{len(statute_ids):,} statutes"
    )

    # BRANCH on the projection account (WAIST #10): read the residual lane and
    # fail-loud on blocking residue (dropped universe members), and reassemble
    # the corpus CoverageCertificate so a silent drop becomes a recorded,
    # checkable fact (the coverage leaf) rather than a clean-looking artifact.
    _emit_projection_residual_branch(label="interlinks", residual_diag_rows=all_diagnostics)
    interlinks_coverage = _corpus_projection_coverage(
        unit="interlink_rows",
        rows=all_rows,
        residual_diag_rows=all_diagnostics,
    )

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl_count = _write_jsonl(out / "lawvm_interlinks.jsonl", all_rows)
    _write_coverage_leaf(out / "lawvm_interlinks.coverage.json", interlinks_coverage)

    if use_parquet:
        ok = _try_write_parquet(
            out / "lawvm_interlinks.parquet",
            all_rows,
            compile_metadata,
            coverage=interlinks_coverage,
        )
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
    overlay_rows, overlay_diagnostics = project_corpus_parallel(
        statute_ids=statute_ids,
        projector_ref=(__name__, "_project_overlays_rows_and_diags"),
        serial_projector=_project_overlays_rows_and_diags,
        store=store,
        workers=workers,
    )
    print(
        f"  surface_overlays: {len(overlay_rows):,} rows over "
        f"{len(statute_ids):,} statutes"
    )
    _emit_projection_residual_branch(
        label="surface_overlays", residual_diag_rows=overlay_diagnostics
    )
    overlays_coverage = _corpus_projection_coverage(
        unit="overlay_rows",
        rows=overlay_rows,
        residual_diag_rows=overlay_diagnostics,
    )
    overlay_jsonl_count = _write_jsonl(out / "lawvm_surface_overlays.jsonl", overlay_rows)
    _write_coverage_leaf(out / "lawvm_surface_overlays.coverage.json", overlays_coverage)
    if use_parquet:
        ok = _try_write_parquet(
            out / "lawvm_surface_overlays.parquet",
            overlay_rows,
            compile_metadata,
            empty_schema_columns=OVERLAY_ROW_COLUMNS,
            coverage=overlays_coverage,
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

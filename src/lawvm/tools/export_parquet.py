"""lawvm export-projections — project canonical LawVM objects into JSONL files.

Produces row projections of LawVM canonical objects suitable for DuckDB
analytics via ``lawvm sql``.  Writes JSONL files to ``.tmp/projections/``
(or ``--data-dir``).

Tables:
    statutes.jsonl  — one row per statute (metadata + aggregate scores)
    sections.jsonl  — one row per section per statute (diff, texts, events)
    findings.jsonl  — one row per evidence finding
    ops.jsonl       — one row per compiled operation

Usage:
    lawvm export-projections
    lawvm export-projections --corpus data/finland/bench_core.csv
    lawvm export-projections --data-dir .tmp/my_projections
    lawvm export-projections --workers 8
"""
from __future__ import annotations

import csv
import json
import sys
import time
import warnings
from collections import Counter
from concurrent.futures import as_completed
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, cast
from lawvm.core.quirks_disposition import QuirksDisposition

# Suppress chatty projection warnings during bulk export
warnings.filterwarnings("ignore", message=".*out-of-order.*")
warnings.filterwarnings("ignore", message=".*duplicate.*label.*")


SECTION_DIFF_FAILED_RULE_ID = "tool_export_projection_section_diff_failed"


# ---------------------------------------------------------------------------
# Corpus loading (same logic as bench.py)
# ---------------------------------------------------------------------------

def _default_corpus_path() -> str:
    here = Path(__file__).resolve()
    lawvm_dir = here.parent.parent.parent.parent
    core = lawvm_dir / "data" / "finland" / "bench_core.csv"
    if core.exists():
        return str(core)
    primary = lawvm_dir / "data" / "finland" / "bench_corpus.csv"
    if primary.exists():
        return str(primary)
    return str(lawvm_dir / ".tmp" / "batch_test_list.csv")


def _load_corpus(corpus_path: str) -> List[Tuple[int, str]]:
    """Load corpus CSV. Format: N,YEAR/NUM (N = amendment count).

    Special value ``corpus_path == "all"`` bypasses the CSV file and
    enumerates the full farchive via ``store.list_statute_ids()``.
    Use this for crosslink / structural projections that need full
    corpus coverage (the ``bench_core.csv`` curated subset is for
    replay-benchmark scoring, not graph projections).
    """
    if corpus_path == "all":
        from lawvm.finland.corpus import get_corpus_store
        store = get_corpus_store()
        ids = list(store.list_statute_ids())
        # Amendment count not needed downstream for projection paths that
        # only care about the statute id; fill with 0 sentinel.
        return [(0, sid) for sid in ids]
    with open(corpus_path, newline="") as f:
        rows = list(csv.reader(f))
    result = []
    for row in rows:
        if len(row) < 2:
            continue
        try:
            count = int(row[0])
            sid = row[1].strip()
        except (ValueError, IndexError):
            continue
        result.append((count, sid))
    return result


def _section_diff_failed_finding(statute_id: str, exc: Exception) -> Dict[str, Any]:
    """Diagnostic row for section projection failures.

    Export projection is a reporting phase: a section diff failure should not
    block statute-level rows, but it must remain visible in the findings table.
    """
    return {
        "statute_id": statute_id,
        "claim_kind": SECTION_DIFF_FAILED_RULE_ID,
        "claim_rule": "EXPORT.SECTION_DIFF_FAILED",
        "section_key": "",
        "severity": "warning",
        "detail": str(exc),
        "rule_id": SECTION_DIFF_FAILED_RULE_ID,
        "phase": "projection",
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": QuirksDisposition.RECORD,
        "section_diff_status": "section_diff_failed",
        "error_type": type(exc).__name__,
    }


# ---------------------------------------------------------------------------
# Per-statute projection worker
# ---------------------------------------------------------------------------

def _project_one_statute(
    statute_id: str,
    amendment_count: int,
    mode: str = "official_consolidation",
) -> Dict[str, Any]:
    """Compute all projection rows for one statute.

    Returns a dict with keys: statute, sections, findings, ops.
    Each value is a list of row dicts ready for JSONL serialization.
    """
    import Levenshtein

    from lawvm.finland.corpus import _oracle_version_label, get_ground_truth
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml
    from lawvm.tools.section_keys import (
        extract_ir_sections,
        extract_oracle_sections,
        reconcile_unique_unscoped_aliases,
    )

    result: Dict[str, Any] = {
        "statute": [],
        "sections": [],
        "findings": [],
        "ops": [],
    }

    compiled_ops: list[Any] = []
    try:
        master = call_replay_xml(
            replay_xml,
            request=ReplayXmlRequest(
                parent_id=statute_id,
                mode=cast(Literal["official_consolidation", "legal_pit"], mode),
                quiet=True,
                build_full_products=True,
            ),
            sinks=ReplayXmlSinks(compiled_ops_out=compiled_ops),
        )
    except (NameError, TypeError, AttributeError):
        raise
    except Exception as exc:
        # Statute failed to compile — emit minimal statute row
        result["statute"].append({
            "statute_id": statute_id,
            "title": "",
            "amendment_count": amendment_count,
            "oracle_version": "",
            "score": -1.0,
            "run_status": str(exc),
            "diff_kind_summary": "",
        })
        return result

    # --- Score: text similarity ---
    try:
        truth = get_ground_truth(statute_id)
    except Exception:
        truth = ""

    replay_text = master.serialize_text() if master else ""

    def _clean(t: str) -> str:
        import re
        t = re.sub(r"\s+", " ", t).strip()
        return t

    c_res = _clean(replay_text)
    c_truth = _clean(truth)
    score = Levenshtein.ratio(c_res, c_truth) if c_truth else -1.0

    # --- Oracle version ---
    oracle_version = ""
    try:
        oracle_version = _oracle_version_label(statute_id) or ""
    except Exception:
        pass

    # --- Section diffs ---
    diff_kind_counter: Counter[str] = Counter()
    try:
        replay_ir = (
            master.materialized_state.ir
            if master and getattr(master, "materialized_state", None) is not None
            else None
        )
        replay_sections = extract_ir_sections(replay_ir) if replay_ir else {}

        from lawvm.finland.corpus import get_ground_truth_tree
        oracle_root = get_ground_truth_tree(statute_id)
        oracle_sections = extract_oracle_sections(oracle_root) if oracle_root is not None else {}
        replay_sections, oracle_sections = reconcile_unique_unscoped_aliases(
            replay_sections, oracle_sections,
        )

        from lawvm.semantic.contracts import build_semantic_support
        from lawvm.semantic.structure import (
            semantic_structure_from_ir,
            semantic_structure_from_oracle,
        )

        for key in sorted(set(replay_sections) | set(oracle_sections)):
            replay_node = replay_sections.get(key)
            oracle_node = oracle_sections.get(key)
            replay_sem = semantic_structure_from_ir(replay_node) if replay_node is not None else None
            oracle_sem = semantic_structure_from_oracle(oracle_node) if oracle_node is not None else None
            support = build_semantic_support(replay_sem, oracle_sem)

            sd = support.get("semantic_diff", {}) if support else {}
            diff_kind = sd.get("kind", "unknown") if sd else "unknown"
            if not support:
                diff_kind = "missing"
            diff_kind_counter[diff_kind] += 1

            # Section texts
            def _sem_text(sem: Any) -> str:
                if sem is None:
                    return ""
                if hasattr(sem, "full_text"):
                    return sem.full_text or ""
                return ""

            oracle_text = _sem_text(oracle_sem)[:2000]
            replay_text_sec = _sem_text(replay_sem)[:2000]

            similarity = -1.0
            if oracle_text and replay_text_sec:
                similarity = Levenshtein.ratio(oracle_text, replay_text_sec)

            events = sd.get("events", []) if sd else []
            events_json = json.dumps(events, ensure_ascii=False) if events else "[]"

            # --- Telos-section flag (#5) ---
            from lawvm.finland.telos_section_flag import classify_telos_section
            telos_result = (
                classify_telos_section(
                    replay_node,
                    replay_node.label,
                    statute_id,
                )
                if replay_node is not None
                else None
            )
            is_purpose_section = bool(telos_result and telos_result.is_purpose_section)
            purpose_text_snippet: Optional[str] = (
                telos_result.purpose_text_snippet if telos_result is not None else None
            )

            result["sections"].append({
                "statute_id": statute_id,
                "section_key": key,
                "diff_kind": diff_kind,
                "oracle_label_basis": sd.get("oracle_label_basis", "") if sd else "",
                "replay_label_basis": sd.get("replay_label_basis", "") if sd else "",
                "oracle_text": oracle_text,
                "replay_text": replay_text_sec,
                "similarity": round(similarity, 6),
                "events": events_json,
                "is_purpose_section": is_purpose_section,
                "purpose_text_snippet": purpose_text_snippet,
            })
    except (NameError, TypeError, AttributeError):
        raise
    except Exception as exc:
        # Section diffs may fail for some statutes; keep the statute row, but
        # do not let the projection failure disappear from the diagnostic lane.
        result["findings"].append(_section_diff_failed_finding(statute_id, exc))

    # --- Diff kind summary ---
    diff_kind_summary = ", ".join(
        f"{k}:{v}" for k, v in sorted(diff_kind_counter.items())
    )

    # --- Ops ---
    for op in compiled_ops:
        target = op.get("target", {})
        result["ops"].append({
            "statute_id": statute_id,
            "amendment_id": op.get("source_statute", ""),
            "op_type": op.get("action", ""),
            "target_kind": target.get("container", ""),
            "target_section": target.get("section", ""),
            "target_chapter": target.get("chapter", ""),
            "target_paragraph": target.get("subsection", ""),
        })

    # --- Findings (lightweight: extract from section support data) ---
    # Full evidence bundles are expensive; extract section-level findings
    # from the semantic support structures we already computed above.
    try:
        for sec_row in result["sections"]:
            diff_kind = sec_row.get("diff_kind", "")
            if diff_kind in ("identical", "missing", "unknown"):
                continue
            # Each non-identical section is a finding
            result["findings"].append({
                "statute_id": statute_id,
                "claim_kind": f"section_diff.{diff_kind}",
                "claim_rule": "EXPORT.SECTION_DIFF",
                "section_key": sec_row.get("section_key", ""),
                "severity": "info" if diff_kind == "editorial_only" else "warning",
                "detail": f"similarity={sec_row.get('similarity', -1)}",
            })
    except (NameError, TypeError, AttributeError):
        raise
    except Exception:
        pass

    # --- Statute row ---
    result["statute"].append({
        "statute_id": statute_id,
        "title": getattr(master, "title", "") or "" if master else "",
        "amendment_count": amendment_count,
        "oracle_version": oracle_version,
        "score": round(score, 6),
        "run_status": "OK",
        "diff_kind_summary": diff_kind_summary,
    })

    return result


def _project_worker(args: Tuple[int, str, str]) -> Tuple[str, Dict[str, Any], float]:
    """Worker for parallel projection — takes (amendment_count, statute_id, mode)."""
    amendment_count, statute_id, mode = args
    t0 = time.time()
    try:
        result = _project_one_statute(statute_id, amendment_count, mode)
    except (NameError, TypeError, AttributeError):
        raise
    except Exception as exc:
        result = {
            "statute": [{
                "statute_id": statute_id,
                "title": "",
                "amendment_count": amendment_count,
                "oracle_version": "",
                "score": -1.0,
                "run_status": str(exc),
                "diff_kind_summary": "",
            }],
            "sections": [],
            "findings": [],
            "ops": [],
        }
    elapsed = time.time() - t0
    return statute_id, result, elapsed


# ---------------------------------------------------------------------------
# JSONL writer
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    """Write rows as JSONL, return count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


# ---------------------------------------------------------------------------
# Parquet writer (optional — falls back to JSONL)
# ---------------------------------------------------------------------------

def _attach_compile_metadata(table: Any, compile_metadata: Any) -> Any:
    """Attach CompileMetadata fields to a pyarrow Table's schema metadata."""
    if compile_metadata is None:
        raise ValueError(
            "export_projections: CompileMetadata is required for v3 substrate-locked "
            "persistence. Construct via build_default_compile_metadata() or "
            "explicitly. See UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13 Step 5."
        )
    existing = table.schema.metadata or {}
    meta = dict(existing)
    for k, v in compile_metadata.to_metadata_dict().items():
        meta[k.encode()] = v.encode()
    return table.replace_schema_metadata(meta)


def _projection_coverage(
    *,
    table_name: str,
    rows: List[Dict[str, Any]],
    corpus_size: int,
) -> Dict[str, Any]:
    """Build the ``projection_coverage`` leaf for one projection table.

    Rank-21 fix (projection plane, silent_drop): the export shipped parquet
    bodies with NO coverage/verifier leaf, so a partial emission (fewer statutes
    than the corpus scanned — e.g. a worker silently dropping a failure) read as
    an all-provision-clean artifact. This declares, PER TABLE, the universe the
    rows are projected over (the corpus statute set), the distinct statutes that
    actually contributed a row, and — for the one-row-per-statute ``statutes``
    table — the omitted count (corpus statutes that produced no statute row).

    Shape mirrors the certificate envelope's ``projection_coverage`` member
    (CERTIFICATE_SCHEMA_V0.md §5.5): ``universe_kind`` names a formal derivation
    a checker can recompute, not a prose string. ``omitted_row_count`` is only a
    DEFECT signal for ``statutes`` (where every corpus statute owes exactly one
    row even on failure); the per-statute fan-out tables (sections/findings/ops)
    legitimately emit zero rows for some statutes, so for them the leaf records
    ``statutes_with_rows`` as an honest derivation, not a defect.
    """
    distinct_statutes = {r.get("statute_id", "") for r in rows}
    distinct_statutes.discard("")
    leaf: Dict[str, Any] = {
        "universe_kind": "corpus_statute_set",
        "address_source": "export_projections.corpus",
        "corpus_size": corpus_size,
        "row_count": len(rows),
        "statutes_with_rows": len(distinct_statutes),
    }
    if table_name == "statutes":
        # One row per corpus statute is owed (the worker emits a minimal row even
        # on compile failure); a shortfall is a real silent drop.
        leaf["omitted_row_count"] = max(0, corpus_size - len(rows))
    else:
        # Per-statute fan-out: zero rows for a statute is not a defect, so no
        # omitted-row claim; statutes_with_rows is the honest coverage account.
        leaf["omitted_row_count"] = 0
    return leaf


def _attach_projection_coverage(table: Any, coverage: Dict[str, Any]) -> Any:
    """Attach the ``projection_coverage`` leaf to a pyarrow Table's metadata.

    Written under the ``lawvm.projection_coverage`` schema-metadata key as JSON
    so a reader (and the freshness/verifier tooling) can recover the per-table
    coverage account from the parquet itself — partial emission can never read as
    all-provision clean.
    """
    existing = table.schema.metadata or {}
    meta = dict(existing)
    meta[b"lawvm.projection_coverage"] = json.dumps(
        coverage, ensure_ascii=False, sort_keys=True
    ).encode()
    return table.replace_schema_metadata(meta)


def _try_write_parquet(
    path: Path,
    rows: List[Dict[str, Any]],
    compile_metadata: Any = None,
    *,
    coverage: Optional[Dict[str, Any]] = None,
) -> bool:
    """Try to write rows as Parquet with optional compile metadata. Returns True if ok.

    ``coverage`` is the per-table :func:`_projection_coverage` leaf; when given it
    is attached to the parquet schema metadata (rank-21 coverage-leaf fix) so the
    artifact carries an honest per-table coverage account at write.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False

    if not rows:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    table = _attach_compile_metadata(table, compile_metadata)
    if coverage is not None:
        table = _attach_projection_coverage(table, coverage)
    pq.write_table(table, str(path))
    return True


# ---------------------------------------------------------------------------
# Main export logic
# ---------------------------------------------------------------------------

def export_projections(
    *,
    corpus_path: Optional[str] = None,
    data_dir: str = ".tmp/projections",
    workers: int = 0,
    mode: str = "official_consolidation",
    limit: Optional[int] = None,
    use_parquet: bool = True,
    include_refs: bool = False,
    include_actors: bool = False,
    include_pools: bool = False,
    include_he_corpus: bool = False,
    include_preparatory_refs: bool = False,
    include_inline_citations: bool = False,
    include_interlinks: bool = False,
    include_sections_text: bool = False,
    he_farchive: Optional[str] = None,
    he_data_dir: Optional[str] = None,
    compile_metadata: Optional[Any] = None,
) -> Dict[str, int]:
    """Export corpus projections to JSONL (and optionally Parquet).

    Returns dict of table name to row count.
    """
    if corpus_path is None:
        corpus_path = "all"  # full farchive; was bench_core.csv (curated bench subset)

    corpus = _load_corpus(corpus_path)
    if not corpus:
        print(f"error: empty corpus at {corpus_path}", file=sys.stderr)
        sys.exit(1)

    if limit:
        corpus = corpus[:limit]

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    total = len(corpus)
    print(f"Exporting projections for {total} statutes to {out}/")

    # Collect all rows
    all_statutes: List[Dict[str, Any]] = []
    all_sections: List[Dict[str, Any]] = []
    all_findings: List[Dict[str, Any]] = []
    all_ops: List[Dict[str, Any]] = []

    if workers <= 0:
        import os
        workers = os.cpu_count() or 1
    # Clamp to 8 even for an explicit --workers: each worker holds a corpus
    # store, so the cap bounds resident memory under the WSL2 ceiling.
    workers = max(1, min(workers, 8))

    if workers > 1:
        from lawvm.tools._worker_pool import managed_executor
        from lawvm.finland.corpus import _get_corpus_store

        # Pre-warm corpus cache in main process for COW sharing
        try:
            _get_corpus_store()
        except Exception:
            pass

        work = [(count, sid, mode) for count, sid in corpus]

        with managed_executor(workers) as pool:
            futures = {
                pool.submit(_project_worker, item): item[1]
                for item in work
            }
            done = 0
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    _, result, elapsed = future.result()
                except (NameError, TypeError, AttributeError):
                    raise
                except Exception as exc:
                    print(f"  FAIL {sid}: {exc}", file=sys.stderr)
                    continue

                all_statutes.extend(result["statute"])
                all_sections.extend(result["sections"])
                all_findings.extend(result["findings"])
                all_ops.extend(result["ops"])

                done += 1
                if done % 50 == 0 or done == total:
                    print(f"  [{done}/{total}] last: {sid} ({elapsed:.1f}s)")
    else:
        for i, (count, sid) in enumerate(corpus, 1):
            _, result, elapsed = _project_worker((count, sid, mode))
            all_statutes.extend(result["statute"])
            all_sections.extend(result["sections"])
            all_findings.extend(result["findings"])
            all_ops.extend(result["ops"])

            if i % 20 == 0 or i == total:
                print(f"  [{i}/{total}] last: {sid} ({elapsed:.1f}s)")

    # Write JSONL (always — DuckDB can read JSONL via read_json_auto)
    counts: Dict[str, int] = {}
    counts["statutes"] = _write_jsonl(out / "statutes.jsonl", all_statutes)
    counts["sections"] = _write_jsonl(out / "sections.jsonl", all_sections)
    counts["findings"] = _write_jsonl(out / "findings.jsonl", all_findings)
    counts["ops"] = _write_jsonl(out / "ops.jsonl", all_ops)

    # Per-table projection_coverage leaf (rank-21): declare, per table, the
    # corpus universe + emitted rows so a partial emission cannot read as
    # all-provision clean. Written both into the parquet schema metadata (below)
    # and as a ``{name}.coverage.json`` sidecar so it is visible even for the
    # always-written JSONL bodies (and when pyarrow is absent).
    coverage_by_table: Dict[str, Dict[str, Any]] = {}
    for name, rows in [
        ("statutes", all_statutes),
        ("sections", all_sections),
        ("findings", all_findings),
        ("ops", all_ops),
    ]:
        leaf = _projection_coverage(
            table_name=name, rows=rows, corpus_size=total
        )
        coverage_by_table[name] = leaf
        (out / f"{name}.coverage.json").write_text(
            json.dumps(leaf, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    # Try Parquet as well (optional upgrade)
    if use_parquet:
        parquet_ok = False
        for name, rows in [
            ("statutes", all_statutes),
            ("sections", all_sections),
            ("findings", all_findings),
            ("ops", all_ops),
        ]:
            if _try_write_parquet(
                out / f"{name}.parquet",
                rows,
                compile_metadata,
                coverage=coverage_by_table[name],
            ):
                parquet_ok = True

        if parquet_ok:
            print("  (Also wrote Parquet files)")

    # --- fi_refs.parquet: ReferenceMention projection ---
    if include_refs:
        print("\nExporting fi_refs projection...")
        from lawvm.tools.export_fi_refs import export_fi_refs
        refs_count = export_fi_refs(
            corpus,
            data_dir=data_dir,
            use_parquet=use_parquet,
            limit=limit,
            compile_metadata=compile_metadata,
        )
        counts["fi_refs"] = refs_count

    # --- fi_actors.parquet: ActorMention projection ---
    if include_actors:
        print("\nExporting fi_actors projection...")
        from lawvm.tools.export_fi_actors import export_fi_actors
        actors_count = export_fi_actors(
            corpus,
            data_dir=data_dir,
            use_parquet=use_parquet,
            limit=limit,
            compile_metadata=compile_metadata,
        )
        counts["fi_actors"] = actors_count

    # --- fi_pools.parquet: PoolMention projection ---
    if include_pools:
        print("\nExporting fi_pools projection...")
        from lawvm.tools.export_fi_pools import export_fi_pools
        pools_count = export_fi_pools(
            corpus,
            data_dir=data_dir,
            use_parquet=use_parquet,
            limit=limit,
            compile_metadata=compile_metadata,
        )
        counts["fi_pools"] = pools_count

    # --- fi_he_corpus/atoms/law_refs/signatures: HE corpus projection ---
    if include_he_corpus:
        print("\nExporting fi_he_corpus projection...")
        from lawvm.tools.export_fi_he_corpus import project_he_corpus
        _he_farchive = he_farchive or "data/fi_government_proposal.farchive"
        _he_data_dir = he_data_dir or "data/fi/v1"
        he_counts = project_he_corpus(
            farchive_path=_he_farchive,
            data_dir=_he_data_dir,
            use_parquet=use_parquet,
            limit=limit,
            compile_metadata=compile_metadata,
        )
        counts.update(he_counts)

    # --- fi_preparatory_refs.parquet: PreparatoryReference projection ---
    if include_preparatory_refs:
        print("\nExporting fi_preparatory_refs projection...")
        from lawvm.tools.export_fi_preparatory_refs import export_fi_preparatory_refs
        prep_count = export_fi_preparatory_refs(
            corpus,
            data_dir=data_dir,
            use_parquet=use_parquet,
            limit=limit,
            compile_metadata=compile_metadata,
        )
        counts["fi_preparatory_refs"] = prep_count

    # --- fi_inline_citations.parquet: InlineCitation body-prose projection ---
    if include_inline_citations:
        print("\nExporting fi_inline_citations projection...")
        from lawvm.tools.export_fi_inline_citations import export_fi_inline_citations
        _he_farchive_path = he_farchive or "data/fi_government_proposal.farchive"
        inline_count = export_fi_inline_citations(
            corpus,
            data_dir=data_dir,
            use_parquet=use_parquet,
            he_farchive_path=_he_farchive_path,
            limit=limit,
            compile_metadata=compile_metadata,
        )
        counts["fi_inline_citations"] = inline_count

    # --- lawvm_interlinks.parquet: jurisdiction-neutral interlink projection ---
    if include_interlinks:
        print("\nExporting lawvm_interlinks projection...")
        from lawvm.tools.export_fi_interlinks import export_fi_interlinks
        interlinks_count = export_fi_interlinks(
            corpus,
            data_dir=data_dir,
            use_parquet=use_parquet,
            limit=limit,
            compile_metadata=compile_metadata,
            workers=workers,
        )
        counts["lawvm_interlinks"] = interlinks_count

    # --- fi_sections_text.parquet: oracle section-text projection ---
    if include_sections_text:
        print("\nExporting fi_sections_text projection...")
        from lawvm.tools.export_fi_sections_text import export_fi_sections_text
        sections_text_count = export_fi_sections_text(
            corpus,
            data_dir=data_dir,
            use_parquet=use_parquet,
            limit=limit,
            compile_metadata=compile_metadata,
        )
        counts["fi_sections_text"] = sections_text_count

    print()
    for name, n in counts.items():
        print(f"  {name}: {n:,} rows")
    print(f"\nProjections written to {out}/")

    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _default_export_compile_metadata(jurisdiction: str) -> Any:
    """Default v3 CompileMetadata stamp for a CLI projection export.

    Parquet persistence is v3 substrate-locked: every written table carries a
    CompileMetadata provenance stamp (UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13
    Step 5), so a CLI caller must supply one. Mirror ``rebuild_indexes``: the
    ``source_bundle_hash`` is the REAL Tier-1 farchive fingerprint for the
    jurisdiction (the same hash the read-side freshness guard computes), so the
    persisted parquet carries faithful provenance — not a sentinel. Degrades to
    a declared ``no-farchive`` only when the farchive is genuinely absent
    (CI / farchive-less environments), never silently.
    """
    from pathlib import Path

    from lawvm.core.compile_metadata_default import build_default_compile_metadata
    from lawvm.tools.rebuild_indexes import _projections_for
    from lawvm.tools.tier2_state import primary_farchive_hash

    specs = _projections_for(jurisdiction)
    tier_1_deps = specs[0].tier_1_deps if specs else ()
    farchive_fp = primary_farchive_hash("data", tier_1_deps) if tier_1_deps else ""
    return build_default_compile_metadata(
        jurisdiction=jurisdiction,
        source_bundle_hash=f"sha256:{farchive_fp or 'no-farchive'}",
        build_id=f"cli.export-projections.{jurisdiction}",
        graph_store_root=Path("data") / jurisdiction,
    )


def main(args: Any) -> None:
    jurisdiction = getattr(args, "jurisdiction", None) or "fi"
    export_projections(
        corpus_path=getattr(args, "corpus", None),
        data_dir=getattr(args, "data_dir", ".tmp/projections"),
        compile_metadata=_default_export_compile_metadata(jurisdiction),
        workers=getattr(args, "workers", 0),
        mode=getattr(args, "mode", "official_consolidation"),
        limit=getattr(args, "limit", None),
        include_refs=getattr(args, "include_refs", False),
        include_actors=getattr(args, "include_actors", False),
        include_pools=getattr(args, "include_pools", False),
        include_he_corpus=getattr(args, "include_he_corpus", False),
        include_preparatory_refs=getattr(args, "include_preparatory_refs", False),
        include_inline_citations=getattr(args, "include_inline_citations", False),
        include_interlinks=getattr(args, "include_interlinks", False),
        include_sections_text=getattr(args, "include_sections_text", False),
        he_farchive=getattr(args, "he_farchive", None),
        he_data_dir=getattr(args, "he_data_dir", None),
    )

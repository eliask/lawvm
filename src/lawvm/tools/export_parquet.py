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
    """Load corpus CSV. Format: N,YEAR/NUM (N = amendment count)."""
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
        "quirks_disposition": "record",
        "status": "section_diff_failed",
        "error_type": type(exc).__name__,
    }


# ---------------------------------------------------------------------------
# Per-statute projection worker
# ---------------------------------------------------------------------------

def _project_one_statute(
    statute_id: str,
    amendment_count: int,
    mode: str = "finlex_oracle",
) -> Dict[str, Any]:
    """Compute all projection rows for one statute.

    Returns a dict with keys: statute, sections, findings, ops.
    Each value is a list of row dicts ready for JSONL serialization.
    """
    import Levenshtein

    from lawvm.finland.grafter import (
        _oracle_version_label,
        get_ground_truth,
        replay_xml,
    )
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

    compiled_ops: list = []
    try:
        master = replay_xml(
            statute_id,
            mode=cast(Literal["finlex_oracle", "legal_pit"], mode),
            quiet=True,
            compiled_ops_out=compiled_ops,
            build_full_products=True,
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
            "status": str(exc),
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
    diff_kind_counter: Counter = Counter()
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
        "status": "OK",
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
                "status": str(exc),
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

def _try_write_parquet(path: Path, rows: List[Dict[str, Any]]) -> bool:
    """Try to write rows as Parquet. Returns True if successful."""
    try:
        import pyarrow as pa  # ty: ignore[unresolved-import]
        import pyarrow.parquet as pq  # ty: ignore[unresolved-import]
    except ImportError:
        return False

    if not rows:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
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
    mode: str = "finlex_oracle",
    limit: Optional[int] = None,
    use_parquet: bool = True,
) -> Dict[str, int]:
    """Export corpus projections to JSONL (and optionally Parquet).

    Returns dict of table name to row count.
    """
    if corpus_path is None:
        corpus_path = _default_corpus_path()

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
        workers = max(1, min(os.cpu_count() or 1, 8))

    if workers > 1:
        from lawvm.tools._worker_pool import managed_executor

        # Pre-warm corpus cache in main process for COW sharing
        try:
            from lawvm.finland.grafter import _get_corpus_store
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

    # Try Parquet as well (optional upgrade)
    if use_parquet:
        parquet_ok = False
        for name, rows in [
            ("statutes", all_statutes),
            ("sections", all_sections),
            ("findings", all_findings),
            ("ops", all_ops),
        ]:
            if _try_write_parquet(out / f"{name}.parquet", rows):
                parquet_ok = True

        if parquet_ok:
            print("  (Also wrote Parquet files)")

    print()
    for name, n in counts.items():
        print(f"  {name}: {n:,} rows")
    print(f"\nProjections written to {out}/")

    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args: Any) -> None:
    export_projections(
        corpus_path=getattr(args, "corpus", None),
        data_dir=getattr(args, "data_dir", ".tmp/projections"),
        workers=getattr(args, "workers", 0),
        mode=getattr(args, "mode", "finlex_oracle"),
        limit=getattr(args, "limit", None),
    )

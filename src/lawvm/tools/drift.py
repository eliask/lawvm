"""lawvm drift — measure content drift in the Finnish law corpus.

Content drift = sections where the base XML encoding differs from the
Finlex oracle encoding, even though NO amendment modified them.  This
is a source-quality issue, not a pipeline accuracy issue.

Usage:
    lawvm drift --statute 2009/953
    lawvm drift --corpus
    lawvm drift --corpus --top 20
    lawvm drift --corpus --output .tmp/drift_results.csv
"""
from __future__ import annotations

import csv
import io
import re
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import Levenshtein
from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.xml_ingest import xml_to_ir_node
from lawvm.corpus_store import get_corpus_store
from lawvm.core.pipeline_capture import CaptureStore
from lawvm.finland.grafter import _fi_label_postprocessor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CAPTURE_DB = Path(".cache/pipeline_gold.db")
DRIFT_THRESHOLD = 0.98  # below this similarity = "drifted"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _collect_sections(ir_node: IRNode) -> dict[str, str]:
    """Recursively collect {label: text} for every section node in the IR tree."""
    result: dict[str, str] = {}
    if ir_node.kind == "section" and ir_node.label:
        result[ir_node.label] = re.sub(r"\s+", " ", irnode_to_text(ir_node)).strip()
    for child in ir_node.children:
        result.update(_collect_sections(child))
    return result


def _parse_body_ir(xml_bytes: bytes) -> IRNode | None:
    """Parse AKN XML bytes into a body IRNode using Finnish label postprocessor."""
    try:
        tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None
    body = tree.find(".//{*}body")
    el = body if body is not None else tree
    with redirect_stdout(io.StringIO()):
        return xml_to_ir_node(el, _fi_label_postprocessor)


def _decade(year: int) -> str:
    return f"{(year // 10) * 10}s"


# ---------------------------------------------------------------------------
# Per-section result
# ---------------------------------------------------------------------------

@dataclass
class SectionResult:
    sid: str
    label: str
    base_text: str
    oracle_text: str
    similarity: float
    touched: bool  # True if any amendment in capture DB addressed this section

    @property
    def drift_candidate(self) -> bool:
        return not self.touched and self.similarity < DRIFT_THRESHOLD

    @property
    def year(self) -> int:
        return int(self.sid.split("/")[0])


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _load_touched_labels(store: CaptureStore) -> dict[str, set[str]]:
    """Return {sid -> set of section labels touched by any amendment}."""
    touched: dict[str, set[str]] = {}
    for sid in store.statutes():
        labels: set[str] = set()
        for cap in store.load(sid):
            labels.update(cap.body_section_labels)
        touched[sid] = labels
    return touched


def _analyse_statute(
    sid: str,
    touched_labels: set[str],
    cs: Any,
) -> list[SectionResult]:
    """Compare base vs oracle for one statute; return per-section results."""
    src_bytes = cs.read_source(sid)
    if src_bytes is None:
        return []
    base_ir = _parse_body_ir(src_bytes)
    if base_ir is None:
        return []
    base_secs = _collect_sections(base_ir)

    oracle_bytes = cs.read_oracle(sid)
    if oracle_bytes is None:
        return []
    try:
        oracle_tree = etree.fromstring(oracle_bytes)
    except etree.XMLSyntaxError:
        return []
    oracle_body = oracle_tree.find(".//{*}body")
    oracle_el = oracle_body if oracle_body is not None else oracle_tree
    oracle_ir = xml_to_ir_node(oracle_el, _fi_label_postprocessor)
    oracle_secs = _collect_sections(oracle_ir)

    results: list[SectionResult] = []
    common = set(base_secs) & set(oracle_secs)
    for label in sorted(common):
        b = base_secs[label]
        o = oracle_secs[label]
        sim = Levenshtein.ratio(b, o)
        results.append(SectionResult(
            sid=sid,
            label=label,
            base_text=b,
            oracle_text=o,
            similarity=sim,
            touched=(label in touched_labels),
        ))
    return results


# ---------------------------------------------------------------------------
# Corpus-mode helpers
# ---------------------------------------------------------------------------

def _run_corpus(
    store: CaptureStore,
    verbose: bool = False,
) -> list[SectionResult]:
    """Run drift analysis across all statutes in the capture DB."""
    touched_by_sid = _load_touched_labels(store)
    cs = get_corpus_store()
    all_results: list[SectionResult] = []
    statutes = store.statutes()
    for i, sid in enumerate(statutes, 1):
        touched = touched_by_sid.get(sid, set())
        try:
            results = _analyse_statute(sid, touched, cs)
        except Exception as exc:
            if verbose:
                print(f"  [{i}/{len(statutes)}] {sid}: SKIPPED ({exc})", file=sys.stderr)
            continue
        if verbose:
            n_drift = sum(1 for r in results if r.drift_candidate)
            n_unt = sum(1 for r in results if not r.touched)
            print(
                f"  [{i}/{len(statutes)}] {sid}: "
                f"{len(results)} sections, {n_unt} untouched, {n_drift} drifted",
                file=sys.stderr,
            )
        all_results.extend(results)
    return all_results


def _print_corpus_summary(
    all_results: list[SectionResult],
    top: int = 20,
) -> None:
    drift_candidates = [r for r in all_results if r.drift_candidate]
    untouched = [r for r in all_results if not r.touched]

    total_sections = len(all_results)
    total_untouched = len(untouched)
    total_drift = len(drift_candidates)
    mean_sim_all = (
        sum(r.similarity for r in all_results) / total_sections
        if total_sections else 0.0
    )
    mean_sim_untouched = (
        sum(r.similarity for r in untouched) / total_untouched
        if total_untouched else 0.0
    )
    drift_rate = total_drift / total_untouched * 100 if total_untouched else 0.0

    statutes_analysed = len({r.sid for r in all_results})
    print(f"Statutes analysed:        {statutes_analysed}")
    print(f"Total sections (base∩oracle): {total_sections}")
    print(f"Untouched sections:       {total_untouched}")
    print(f"Drift candidates (sim < {DRIFT_THRESHOLD}): {total_drift}  ({drift_rate:.1f}% of untouched)")
    print(f"Mean similarity — all:    {mean_sim_all:.4f}")
    print(f"Mean similarity — untouched: {mean_sim_untouched:.4f}")
    print()

    # Per-decade breakdown
    decade_buckets: dict[str, dict] = {}
    for r in untouched:
        d = _decade(r.year)
        if d not in decade_buckets:
            decade_buckets[d] = {"total": 0, "drifted": 0, "sims": []}
        decade_buckets[d]["total"] += 1
        decade_buckets[d]["sims"].append(r.similarity)
        if r.similarity < DRIFT_THRESHOLD:
            decade_buckets[d]["drifted"] += 1

    if decade_buckets:
        print("Per-decade breakdown (untouched sections):")
        print(f"  {'Decade':<8}  {'Untouched':>10}  {'Drifted':>8}  {'Drift%':>7}  {'Mean sim':>9}")
        for decade in sorted(decade_buckets):
            b = decade_buckets[decade]
            n = b["total"]
            nd = b["drifted"]
            pct = nd / n * 100 if n else 0.0
            ms = sum(b["sims"]) / n if n else 0.0
            print(f"  {decade:<8}  {n:>10}  {nd:>8}  {pct:>6.1f}%  {ms:>9.4f}")
        print()

    # Worst N statutes
    statute_groups: dict[str, list[SectionResult]] = {}
    for r in untouched:
        statute_groups.setdefault(r.sid, []).append(r)

    stat_rows = []
    for sid, group in statute_groups.items():
        n = len(group)
        nd = sum(1 for r in group if r.similarity < DRIFT_THRESHOLD)
        ms = sum(r.similarity for r in group) / n if n else 0.0
        stat_rows.append((sid, group[0].year, n, nd, ms))

    stat_rows.sort(key=lambda x: x[4])  # sort by mean sim ascending (worst first)

    if top > 0:
        shown = stat_rows[:top]
        print(f"Worst {top} statutes by mean similarity (untouched sections):")
        print(f"  {'Statute':<14}  {'Year':>4}  {'Untouched':>10}  {'Drifted':>8}  {'Drift%':>7}  {'Mean sim':>9}")
        for sid, year, n, nd, ms in shown:
            pct = nd / n * 100 if n else 0.0
            print(f"  {sid:<14}  {year:>4}  {n:>10}  {nd:>8}  {pct:>6.1f}%  {ms:>9.4f}")


def _write_csv(all_results: list[SectionResult], output_path: Path) -> None:
    """Write per-statute drift summary CSV."""
    statute_groups: dict[str, list[SectionResult]] = {}
    for r in all_results:
        statute_groups.setdefault(r.sid, []).append(r)

    rows = []
    for sid, group in statute_groups.items():
        untouched = [r for r in group if not r.touched]
        n = len(untouched)
        nd = sum(1 for r in untouched if r.similarity < DRIFT_THRESHOLD)
        ms = sum(r.similarity for r in untouched) / n if n else 0.0
        rows.append({
            "sid": sid,
            "year": group[0].year,
            "total_sections": len(group),
            "untouched_sections": n,
            "drifted_sections": nd,
            "drift_pct": round(nd / n * 100, 2) if n else 0.0,
            "mean_sim_untouched": round(ms, 6),
        })

    rows.sort(key=lambda x: x["mean_sim_untouched"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["sid", "year", "total_sections", "untouched_sections",
                        "drifted_sections", "drift_pct", "mean_sim_untouched"],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"CSV written to: {output_path}")


# ---------------------------------------------------------------------------
# Single-statute mode
# ---------------------------------------------------------------------------

def _run_single(
    sid: str,
    store: CaptureStore | None,
    verbose: bool = False,
) -> None:
    """Analyse one statute and print section-level drift detail."""
    cs = get_corpus_store()

    if store is not None and CAPTURE_DB.exists():
        # Build touched set from capture DB
        touched: set[str] = set()
        for cap in store.load(sid):
            touched.update(cap.body_section_labels)
    else:
        touched = set()

    try:
        results = _analyse_statute(sid, touched, cs)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not results:
        print(f"No sections found for {sid} (missing from corpus store?).")
        return

    drift_cands = [r for r in results if r.drift_candidate]
    untouched = [r for r in results if not r.touched]
    mean_sim = sum(r.similarity for r in results) / len(results)

    print(f"Statute:   {sid}")
    print(f"Sections:  {len(results)} total, {len(untouched)} untouched, "
          f"{len(drift_cands)} drift candidates")
    print(f"Mean sim:  {mean_sim:.4f}")
    print(f"Threshold: sim < {DRIFT_THRESHOLD}")
    print()

    if verbose:
        print(f"{'Label':<10}  {'Sim':>6}  {'Touched':>8}  {'Base len':>9}  {'Oracle len':>10}")
        for r in results:
            flag = "DRIFT" if r.drift_candidate else ("touched" if r.touched else "ok")
            print(
                f"  {r.label:<10}  {r.similarity:>6.4f}  {str(r.touched):>8}  "
                f"{len(r.base_text):>9}  {len(r.oracle_text):>10}  [{flag}]"
            )
    else:
        if drift_cands:
            drift_cands.sort(key=lambda r: r.similarity)
            print("Drift candidates (untouched, sim < threshold):")
            print(f"  {'Label':<10}  {'Sim':>6}  {'Base len':>9}  {'Oracle len':>10}")
            for r in drift_cands:
                print(
                    f"  {r.label:<10}  {r.similarity:>6.4f}  "
                    f"{len(r.base_text):>9}  {len(r.oracle_text):>10}"
                )
        else:
            print("No drift candidates found.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args: Any) -> None:
    statute_id: str | None = getattr(args, "statute", None)
    corpus_mode: bool = getattr(args, "corpus", False)
    top: int = getattr(args, "top", 20)
    output: str | None = getattr(args, "output", None)
    verbose: bool = getattr(args, "verbose", False)

    if not statute_id and not corpus_mode:
        print(
            "error: specify --statute SID for single-statute analysis "
            "or --corpus for full-corpus summary.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load capture store if the DB exists (needed for touched-label tracking)
    store: CaptureStore | None = None
    if CAPTURE_DB.exists():
        store = CaptureStore(str(CAPTURE_DB))
    else:
        print(
            f"note: capture DB not found at {CAPTURE_DB}; "
            "all sections will be treated as untouched.",
            file=sys.stderr,
        )

    if statute_id:
        _run_single(statute_id, store, verbose=verbose)
        return

    # Corpus mode
    if store is None:
        print(
            f"error: corpus mode requires a capture DB at {CAPTURE_DB}.\n"
            "Run `lawvm capture` or scripts/capture_gold.py to populate it.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Running corpus drift analysis ({CAPTURE_DB}) ...", file=sys.stderr)
    all_results = _run_corpus(store, verbose=verbose)

    if not all_results:
        print("No sections analysed — is the capture DB empty?")
        return

    _print_corpus_summary(all_results, top=top)

    if output:
        _write_csv(all_results, Path(output))

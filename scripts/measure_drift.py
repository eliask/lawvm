"""measure_drift.py — Quantify content drift in the Finnish law corpus.

Content drift = sections of a statute where the base XML encoding differs from
the Finlex oracle encoding, even though NO amendment modified them.  This is a
source-quality issue, not a pipeline issue.

Usage (from LawVM/):
    uv run python scripts/measure_drift.py

Output: .tmp/content_drift_report.md
"""
from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")

import Levenshtein
from lxml import etree

from lawvm.core.ir import IRNode, xml_to_ir_node
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.corpus_store import get_corpus_store
from lawvm.core.pipeline_capture import CaptureStore
from lawvm.finland.grafter import _fi_label_postprocessor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CAPTURE_DB = Path(".cache/pipeline_gold.db")
OUTPUT_REPORT = Path(".tmp/content_drift_report.md")
DRIFT_THRESHOLD = 0.98  # below this similarity = "drifted"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def collect_sections(ir_node: IRNode) -> dict[str, str]:
    """Recursively collect {label: text} for every section node in the IR tree."""
    result: dict[str, str] = {}
    if ir_node.kind == "section" and ir_node.label:
        result[ir_node.label] = re.sub(r"\s+", " ", irnode_to_text(ir_node)).strip()
    for child in ir_node.children:
        result.update(collect_sections(child))
    return result


def _parse_body_ir(xml_bytes: bytes) -> IRNode | None:
    """Parse AKN XML bytes → body IRNode using Finnish label postprocessor."""
    try:
        tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None
    body = tree.find(".//{*}body")
    el = body if body is not None else tree
    # Suppress any stdout from xml_to_ir_node (there shouldn't be any, but
    # grafter helpers occasionally print debug lines)
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
# Main analysis
# ---------------------------------------------------------------------------

def load_touched_labels(store: CaptureStore) -> dict[str, set[str]]:
    """Return {sid -> set of section labels touched by any amendment}."""
    touched: dict[str, set[str]] = {}
    for sid in store.statutes():
        labels: set[str] = set()
        for cap in store.load(sid):
            labels.update(cap.body_section_labels)
        touched[sid] = labels
    return touched


def analyse_statute(
    sid: str,
    touched_labels: set[str],
    cs,
) -> list[SectionResult]:
    """Compare base vs oracle for one statute; return per-section results."""
    # --- base ---
    src_bytes = cs.read_source(sid)
    if src_bytes is None:
        return []
    base_ir = _parse_body_ir(src_bytes)
    if base_ir is None:
        return []
    base_secs = collect_sections(base_ir)

    # --- oracle ---
    # get_ground_truth_tree() reads oracle bytes internally; we replicate the
    # call here so we can catch None without triggering internal error paths.
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
    oracle_secs = collect_sections(oracle_ir)

    # --- compare sections present in BOTH base and oracle ---
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
# Report generation
# ---------------------------------------------------------------------------

def build_report(all_results: list[SectionResult]) -> str:
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

    # --- per-decade breakdown of drift candidates ---
    decade_buckets: dict[str, dict] = {}
    for r in untouched:
        d = _decade(r.year)
        if d not in decade_buckets:
            decade_buckets[d] = {"total": 0, "drifted": 0, "sims": []}
        decade_buckets[d]["total"] += 1
        decade_buckets[d]["sims"].append(r.similarity)
        if r.similarity < DRIFT_THRESHOLD:
            decade_buckets[d]["drifted"] += 1

    # --- worst 20 drift candidates ---
    worst = sorted(drift_candidates, key=lambda r: r.similarity)[:20]

    lines: list[str] = []
    lines.append("# Content Drift Report — Finnish Law Corpus")
    lines.append("")
    lines.append(
        "Content drift = sections where base XML and oracle XML differ despite "
        "no amendment in the capture DB having addressed them.  "
        "This is a source-encoding quality issue, not a pipeline issue."
    )
    lines.append("")
    lines.append(f"**Drift threshold:** similarity < {DRIFT_THRESHOLD}")
    lines.append(f"**Capture DB:** {CAPTURE_DB}  (50 statutes, 404 amendments)")
    lines.append("")

    # Summary stats
    lines.append("## Summary Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Statutes analysed | {len({r.sid for r in all_results})} |")
    lines.append(f"| Total sections (base ∩ oracle) | {total_sections} |")
    lines.append(f"| Untouched sections (not in any amendment body) | {total_untouched} |")
    lines.append(f"| Drift candidates (untouched + sim < {DRIFT_THRESHOLD}) | {total_drift} |")
    lines.append(f"| Drift rate (of untouched) | {total_drift/total_untouched*100:.1f}% |" if total_untouched else "| Drift rate | N/A |")
    lines.append(f"| Mean similarity — all sections | {mean_sim_all:.4f} |")
    lines.append(f"| Mean similarity — untouched sections | {mean_sim_untouched:.4f} |")
    lines.append("")

    # Per-decade breakdown
    lines.append("## Per-Decade Breakdown (untouched sections only)")
    lines.append("")
    lines.append("| Decade | Untouched § | Drifted § | Drift % | Mean sim |")
    lines.append("|--------|-------------|-----------|---------|----------|")
    for decade in sorted(decade_buckets):
        b = decade_buckets[decade]
        n = b["total"]
        nd = b["drifted"]
        pct = nd / n * 100 if n else 0.0
        ms = sum(b["sims"]) / n if n else 0.0
        lines.append(f"| {decade} | {n} | {nd} | {pct:.1f}% | {ms:.4f} |")
    lines.append("")

    # Worst 20
    lines.append(f"## Worst 20 Drifting Sections (similarity < {DRIFT_THRESHOLD}, untouched)")
    lines.append("")
    if worst:
        lines.append("| Statute | § | Similarity | Base len | Oracle len |")
        lines.append("|---------|---|-----------|----------|------------|")
        for r in worst:
            lines.append(
                f"| {r.sid} | {r.label} | {r.similarity:.4f} "
                f"| {len(r.base_text)} | {len(r.oracle_text)} |"
            )
    else:
        lines.append("_No drift candidates found._")
    lines.append("")

    # Per-statute drift summary
    lines.append("## Per-Statute Drift Summary")
    lines.append("")
    lines.append("| Statute | Year | Untouched § | Drifted § | Drift % | Mean sim (untouched) |")
    lines.append("|---------|------|-------------|-----------|---------|----------------------|")
    statute_groups: dict[str, list[SectionResult]] = {}
    for r in untouched:
        statute_groups.setdefault(r.sid, []).append(r)
    for sid in sorted(statute_groups):
        group = statute_groups[sid]
        n = len(group)
        nd = sum(1 for r in group if r.similarity < DRIFT_THRESHOLD)
        pct = nd / n * 100 if n else 0.0
        ms = sum(r.similarity for r in group) / n if n else 0.0
        year = group[0].year
        lines.append(f"| {sid} | {year} | {n} | {nd} | {pct:.1f}% | {ms:.4f} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Loading capture DB: {CAPTURE_DB}")
    if not CAPTURE_DB.exists():
        sys.exit(f"ERROR: {CAPTURE_DB} does not exist")

    store = CaptureStore(str(CAPTURE_DB))
    stats = store.stats()
    print(f"  {stats['statutes']} statutes, {stats['total_amendments']} amendments")

    touched_by_sid = load_touched_labels(store)
    cs = get_corpus_store()

    all_results: list[SectionResult] = []
    statutes = store.statutes()
    for i, sid in enumerate(statutes, 1):
        touched = touched_by_sid.get(sid, set())
        try:
            results = analyse_statute(sid, touched, cs)
        except Exception as exc:
            print(f"  [{i}/{len(statutes)}] {sid}: SKIPPED ({exc})")
            continue
        n_drift = sum(1 for r in results if r.drift_candidate)
        n_unt = sum(1 for r in results if not r.touched)
        print(
            f"  [{i}/{len(statutes)}] {sid}: "
            f"{len(results)} sections, {n_unt} untouched, {n_drift} drifted"
        )
        all_results.extend(results)

    print(f"\nTotal sections analysed: {len(all_results)}")
    drift = [r for r in all_results if r.drift_candidate]
    print(f"Drift candidates: {len(drift)}")

    report = build_report(all_results)
    OUTPUT_REPORT.parent.mkdir(exist_ok=True)
    OUTPUT_REPORT.write_text(report, encoding="utf-8")
    print(f"\nReport written to: {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()

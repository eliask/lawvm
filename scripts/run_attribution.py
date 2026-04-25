#!/usr/bin/env python3
"""Run verify-chain on worst-performing statutes for causal error attribution.

Usage (from LawVM/ dir):
    nice -n 19 uv run python scripts/run_attribution.py [--top 300] [--workers 8] [--min-score 0.95]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

BENCH_DIR = Path(__file__).parent.parent / "data" / "bench_runs"
OUTPUT_DIR = Path(__file__).parent.parent / ".tmp" / "verify_chain"
SUMMARY_CSV = Path(__file__).parent.parent / ".tmp" / "attribution_summary.csv"


def _find_latest_bench() -> Path:
    csvs = sorted(BENCH_DIR.glob("*.csv"), key=lambda p: p.stem)
    if not csvs:
        raise FileNotFoundError("No bench runs in data/bench_runs/")
    return csvs[-1]


def _load_worst(bench_csv: Path, min_score: float, top: int) -> list[tuple[str, float]]:
    with open(bench_csv) as f:
        rows = list(csv.reader(f))
    scored = []
    for r in rows[1:]:
        if len(r) >= 3:
            try:
                sid, score = r[1], float(r[2])
            except (ValueError, IndexError):
                continue
            if score < min_score:
                scored.append((sid, score))
    scored.sort(key=lambda x: x[1])
    return scored[:top]


def _run_one(sid: str) -> dict:
    """Run verify-chain via subprocess, parse JSON output."""
    import subprocess
    out_path = OUTPUT_DIR / f"{sid.replace('/', '_')}.json"

    # Skip if already computed
    if out_path.exists():
        try:
            with open(out_path) as f:
                data = json.load(f)
            return {"sid": sid, "status": "ok", "result": data}
        except json.JSONDecodeError:
            pass  # re-run

    result = subprocess.run(
        ["uv", "run", "lawvm", "verify-chain", sid],
        capture_output=True, text=True, timeout=120,
    )
    if out_path.exists():
        try:
            with open(out_path) as f:
                data = json.load(f)
            return {"sid": sid, "status": "ok", "result": data}
        except json.JSONDecodeError:
            return {"sid": sid, "status": "error", "error": "bad JSON"}
    return {"sid": sid, "status": "error", "error": result.stderr[:200]}


def _classify_section(statuses: list[str]) -> str:
    """Classify a section's error from its verify-chain status sequence.

    The status sequence is per-amendment: ok/NEW/modified/MISS/-.
    """
    if "MISS" in statuses:
        # Section exists in oracle but not in replay at final state
        # Find where it went wrong
        last_ok = -1
        for i, s in enumerate(statuses):
            if s in ("ok", "NEW", "modified"):
                last_ok = i
        if last_ok == -1:
            return "EXTRACTION_MISS"  # never appeared in replay
        else:
            return "EXTRACTION_MISS"  # appeared then lost (missed later amendment)
    if "modified" in statuses and statuses[-1] == "modified":
        return "CONTENT_DRIFT"  # section exists but text differs
    return "CORRECT"


def _aggregate_results(results: list[dict]) -> dict:
    """Aggregate verify-chain results into component attribution."""
    component_counts = Counter()
    section_details = []

    for r in results:
        if r["status"] != "ok" or not r.get("result"):
            component_counts["RUN_ERROR"] += 1
            continue

        result = r["result"]
        sid = r["sid"]

        # Parse the matrix: result["sections"] = {label: [status_per_amendment]}
        sections = result.get("sections", {})
        for label, statuses in sections.items():
            cls = _classify_section(statuses)
            component_counts[cls] += 1
            if cls != "CORRECT":
                section_details.append({
                    "statute_id": sid,
                    "section": label,
                    "component": cls,
                    "statuses": statuses,
                })

    return {
        "counts": dict(component_counts),
        "details": section_details,
    }


def main():
    parser = argparse.ArgumentParser(description="Causal error attribution via verify-chain")
    parser.add_argument("--top", type=int, default=300, help="Number of worst statutes (default: 300)")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers (default: 8)")
    parser.add_argument("--min-score", type=float, default=0.95, help="Score threshold (default: 0.95)")
    parser.add_argument("--bench", type=str, default="", help="Bench CSV path (default: latest)")
    args = parser.parse_args()

    bench_csv = Path(args.bench) if args.bench else _find_latest_bench()
    worst = _load_worst(bench_csv, args.min_score, args.top)
    print(f"Attribution run: {len(worst)} statutes below {args.min_score:.0%} from {bench_csv.name}")
    print(f"Workers: {args.workers}")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    results = []
    done = 0

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_one, sid): (sid, score) for sid, score in worst}
        for fut in as_completed(futures):
            sid, score = futures[fut]
            done += 1
            try:
                r = fut.result()
                results.append(r)
                status = r["status"]
            except Exception as e:
                status = "error"
                results.append({"sid": sid, "status": "error", "error": str(e)})

            if done % 10 == 0 or done == len(worst):
                elapsed = time.monotonic() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(worst) - done) / rate if rate > 0 else 0
                print(f"[{done:>4}/{len(worst)}] {sid:<16} {status:>5} "
                      f"({rate:.1f}/s, ETA {eta/60:.0f}m)")

    # Aggregate
    agg = _aggregate_results(results)

    # Print summary
    elapsed = time.monotonic() - start
    print(f"\nDone in {elapsed/60:.1f}m")
    print("\n=== Component Attribution ===")
    for comp, count in sorted(agg["counts"].items(), key=lambda x: -x[1]):
        print(f"  {comp:<25} {count:>6}")

    # Save details
    if agg["details"]:
        with open(SUMMARY_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["statute_id", "section", "component", "statuses"])
            w.writeheader()
            for d in agg["details"]:
                d2 = dict(d)
                d2["statuses"] = "|".join(d2["statuses"])
                w.writerow(d2)
        print(f"\nDetails saved: {SUMMARY_CSV}")

    # Save raw results
    raw_path = OUTPUT_DIR / "attribution_raw.json"
    with open(raw_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Raw results: {raw_path}")


if __name__ == "__main__":
    main()

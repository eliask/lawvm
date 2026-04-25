"""lawvm bench-report — summarise a bench run CSV without re-running the bench.

Usage:
    lawvm bench-report                         # latest run in data/bench_runs/
    lawvm bench-report --run <filename>        # specific CSV file
    lawvm bench-report --bottom 20             # N worst-scoring statutes
    lawvm bench-report --top 20                # N best-scoring statutes
    lawvm bench-report --threshold 0.999       # only show statutes below this
    lawvm bench-report --errors-only           # only show status != OK rows
    lawvm bench-report --json                  # emit JSON
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


def _load_rows(run_arg: str) -> tuple[Path, list[dict[str, str]]]:
    if run_arg:
        path = Path(run_arg)
    else:
        candidates = sorted(Path("data/bench_runs").glob("*.csv"), reverse=True)
        if not candidates:
            print("No CSV files found in data/bench_runs/", file=sys.stderr)
            sys.exit(1)
        path = candidates[0]

    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append(row)
    return path, rows


def main(args: argparse.Namespace) -> None:
    path, rows = _load_rows(args.run)

    # Apply --errors-only filter first
    if args.errors_only:
        rows = [r for r in rows if r.get("status", "OK") != "OK"]

    # Compute summary stats over the (filtered) full set
    total = len(rows)
    if total == 0:
        print("No rows to report.")
        return

    similarities = [float(r["similarity"]) for r in rows]
    mean_sim = sum(similarities) / total
    below_threshold = sum(1 for s in similarities if s < args.threshold)
    error_count = sum(1 for r in rows if r.get("status", "OK") != "OK")

    if args.json:
        output: list[dict[str, Any]] = [
            {
                "statute_id": r["statute_id"],
                "similarity": float(r["similarity"]),
                "status": r.get("status", ""),
                "amendments": int(r.get("amendments", 0)),
                "elapsed_s": float(r.get("elapsed_s", 0)),
            }
            for r in rows
        ]
        print(json.dumps(output, indent=2))
        return

    print(f"Run: {path}")
    print(f"Total: {total}  Mean similarity: {mean_sim:.6f}  "
          f"Below {args.threshold}: {below_threshold}  Errors: {error_count}")
    print()

    # Determine which rows to display
    if args.top > 0:
        display = sorted(rows, key=lambda r: float(r["similarity"]), reverse=True)[: args.top]
        label = f"Top {args.top} best-scoring statutes"
    else:
        n = args.bottom
        display = sorted(rows, key=lambda r: float(r["similarity"]))[: n]
        label = f"Bottom {n} worst-scoring statutes"

    print(f"{label}:")
    print(f"  {'statute_id':<18}  {'similarity':>10}  {'status':<8}  {'amendments':>10}")
    for r in display:
        print(
            f"  {r['statute_id']:<18}  {float(r['similarity']):>10.6f}"
            f"  {r.get('status', ''):<8}  {int(r.get('amendments', 0)):>10}"
        )

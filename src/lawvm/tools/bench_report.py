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


def _first_present_column(
    rows: list[dict[str, str]],
    candidates: tuple[str, ...],
) -> str:
    if not rows:
        return ""
    fieldnames = set(rows[0])
    for candidate in candidates:
        if candidate in fieldnames:
            return candidate
    return ""


def _float_or_zero(value: str | None) -> float:
    if value in {None, ""}:
        return 0.0
    return float(value)


def _int_or_zero(value: str | None) -> int:
    if value in {None, ""}:
        return 0
    return int(float(value))


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

    score_column = _first_present_column(rows, ("similarity", "score", "replay_score"))
    if not score_column:
        print(
            "Bench CSV has no recognized score column "
            "(expected one of: similarity, score, replay_score).",
            file=sys.stderr,
        )
        sys.exit(1)
    count_column = _first_present_column(rows, ("amendments", "n_effects", "n_ops"))
    elapsed_column = _first_present_column(
        rows,
        ("elapsed_s", "duration_s", "phase_total_s"),
    )

    scores = [_float_or_zero(r.get(score_column)) for r in rows]
    mean_score = sum(scores) / total
    below_threshold = sum(1 for score in scores if score < args.threshold)
    error_count = sum(1 for r in rows if r.get("status", "OK") != "OK")

    if args.json:
        output: dict[str, Any] = {
            "run": str(path),
            "score_column": score_column,
            "count_column": count_column,
            "elapsed_column": elapsed_column,
            "rows": [
                {
                    "statute_id": r["statute_id"],
                    "score": _float_or_zero(r.get(score_column)),
                    "status": r.get("status", ""),
                    "count": _int_or_zero(r.get(count_column)) if count_column else 0,
                    "elapsed_s": (
                        _float_or_zero(r.get(elapsed_column))
                        if elapsed_column
                        else 0.0
                    ),
                }
                for r in rows
            ],
        }
        print(json.dumps(output, indent=2))
        return

    print(f"Run: {path}")
    print(f"Score column: {score_column}")
    print(
        f"Total: {total}  Mean score: {mean_score:.6f}  "
        f"Below {args.threshold}: {below_threshold}  Errors: {error_count}"
    )
    print()

    # Determine which rows to display
    if args.top > 0:
        display = sorted(
            rows,
            key=lambda r: _float_or_zero(r.get(score_column)),
            reverse=True,
        )[: args.top]
        label = f"Top {args.top} best-scoring statutes"
    else:
        n = args.bottom
        display = sorted(rows, key=lambda r: _float_or_zero(r.get(score_column)))[: n]
        label = f"Bottom {n} worst-scoring statutes"

    print(f"{label}:")
    print(f"  {'statute_id':<18}  {'score':>10}  {'status':<8}  {'count':>10}")
    for r in display:
        print(
            f"  {r['statute_id']:<18}  {_float_or_zero(r.get(score_column)):>10.6f}"
            f"  {r.get('status', ''):<8}  "
            f"{(_int_or_zero(r.get(count_column)) if count_column else 0):>10}"
        )

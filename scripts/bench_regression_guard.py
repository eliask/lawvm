#!/usr/bin/env python3
"""
Regression guard for LawVM bench runs.

Compares two bench run CSVs and exits 1 if regressions exceed the threshold.

Usage (from LawVM/ dir):
    uv run python scripts/bench_regression_guard.py --baseline v_allin1 --current v_typing1
"""
import argparse
import csv
import sys
from pathlib import Path


BENCH_RUNS_DIR = Path(__file__).parent.parent / "data" / "bench_runs"


def find_csv_by_label(label: str) -> Path:
    """Find the most recent CSV whose filename ends with _{label}.csv."""
    matches = sorted(BENCH_RUNS_DIR.glob(f"*_{label}.csv"))
    if not matches:
        raise FileNotFoundError(
            f"No bench run CSV found for label '{label}' in {BENCH_RUNS_DIR}"
        )
    # sorted() on YYYYMMDDTHHSS_ prefix gives chronological order; take last
    return matches[-1]


def load_scores(path: Path) -> dict[str, float]:
    """Load {statute_id -> similarity} from a bench run CSV."""
    scores: dict[str, float] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or header-less CSV: {path}")
        required = {"statute_id", "similarity"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"CSV {path} missing expected columns: {missing}. "
                f"Found: {reader.fieldnames}"
            )
        for row in reader:
            sid = row["statute_id"].strip()
            try:
                score = float(row["similarity"])
            except ValueError:
                # Skip rows with non-numeric similarity (e.g. ERROR rows)
                continue
            scores[sid] = score
    return scores


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare two bench run CSVs and fail if regressions exceed threshold."
    )
    parser.add_argument("--baseline", required=True, help="Baseline bench run label")
    parser.add_argument("--current", required=True, help="Current bench run label")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.005,
        help="Per-statute regression threshold (default: 0.005)",
    )
    parser.add_argument(
        "--max-regressions",
        type=int,
        default=3,
        help="Max allowed statutes regressing beyond threshold (default: 3)",
    )
    args = parser.parse_args()

    # Locate CSVs
    try:
        baseline_path = find_csv_by_label(args.baseline)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    try:
        current_path = find_csv_by_label(args.current)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Baseline : {baseline_path.name}")
    print(f"Current  : {current_path.name}")
    print()

    # Load scores
    try:
        baseline_scores = load_scores(baseline_path)
        current_scores = load_scores(current_path)
    except (ValueError, OSError) as e:
        print(f"ERROR loading CSV: {e}", file=sys.stderr)
        return 1

    # Compute diffs for statutes present in both runs
    common = sorted(set(baseline_scores) & set(current_scores))
    baseline_only = sorted(set(baseline_scores) - set(current_scores))
    current_only = sorted(set(current_scores) - set(baseline_scores))

    diffs: list[tuple[str, float, float, float]] = []  # (sid, old, new, delta)
    for sid in common:
        old = baseline_scores[sid]
        new = current_scores[sid]
        diffs.append((sid, old, new, new - old))

    # Aggregate stats (over common statutes)
    old_scores = [d[1] for d in diffs]
    new_scores = [d[2] for d in diffs]
    old_mean = mean(old_scores)
    new_mean = mean(new_scores)
    agg_delta = new_mean - old_mean

    print(f"Statutes in baseline : {len(baseline_scores)}")
    print(f"Statutes in current  : {len(current_scores)}")
    print(f"Common statutes      : {len(common)}")
    if baseline_only:
        print(f"Baseline-only        : {len(baseline_only)}  (not in current run)")
    if current_only:
        print(f"Current-only         : {len(current_only)}  (new in current run)")
    print()

    # Aggregate summary
    sign = "+" if agg_delta >= 0 else ""
    print("Aggregate score (common statutes):")
    print(f"  Baseline mean : {old_mean:.6f}")
    print(f"  Current mean  : {new_mean:.6f}")
    print(f"  Delta         : {sign}{agg_delta:.6f}")
    print()

    # Per-statute regressions and improvements
    regressions = sorted(
        [(sid, old, new, delta) for sid, old, new, delta in diffs if delta < -args.threshold],
        key=lambda x: x[3],  # most severe first
    )
    improvements = sorted(
        [(sid, old, new, delta) for sid, old, new, delta in diffs if delta > args.threshold],
        key=lambda x: -x[3],  # largest improvement first
    )

    if regressions:
        print(
            f"Regressions > {args.threshold:.4f} : {len(regressions)} statute(s)"
            f"  (max allowed: {args.max_regressions})"
        )
        print(f"  {'Statute':<20} {'Baseline':>10} {'Current':>10} {'Delta':>10}")
        print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*10}")
        for sid, old, new, delta in regressions:
            print(f"  {sid:<20} {old:>10.6f} {new:>10.6f} {delta:>+10.6f}")
        print()
    else:
        print(f"Regressions > {args.threshold:.4f} : 0  (none)")
        print()

    if improvements:
        print(f"Improvements > {args.threshold:.4f} : {len(improvements)} statute(s)")
        print(f"  {'Statute':<20} {'Baseline':>10} {'Current':>10} {'Delta':>10}")
        print(f"  {'-'*20} {'-'*10} {'-'*10} {'-'*10}")
        for sid, old, new, delta in improvements:
            print(f"  {sid:<20} {old:>10.6f} {new:>10.6f} {delta:>+10.6f}")
        print()
    else:
        print(f"Improvements > {args.threshold:.4f} : 0  (none)")
        print()

    # Verdict
    fail = False
    fail_reasons: list[str] = []

    # Rule 1: aggregate drop > 0.5% always fails
    AGG_HARD_LIMIT = 0.005
    if agg_delta < -AGG_HARD_LIMIT:
        fail = True
        fail_reasons.append(
            f"Aggregate score dropped {agg_delta:.6f} (>{AGG_HARD_LIMIT:.3f} hard limit)"
        )

    # Rule 2: per-statute regression count exceeds max
    if len(regressions) > args.max_regressions:
        fail = True
        fail_reasons.append(
            f"{len(regressions)} statutes regressed beyond threshold "
            f"(max allowed: {args.max_regressions})"
        )

    if fail:
        print("RESULT: FAIL")
        for reason in fail_reasons:
            print(f"  - {reason}")
        return 1
    else:
        print("RESULT: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())

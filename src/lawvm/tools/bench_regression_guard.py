"""lawvm bench-regression-guard — compare saved bench runs and fail on regressions."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


_REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_RUNS_DIR = _REPO_ROOT / "data" / "bench_runs"
EE_BENCH_RUNS_DIR = _REPO_ROOT / "data" / "ee_bench_runs"


def find_csv_by_label(label: str, jurisdiction: str = "fi") -> Path:
    """Find the most recent run CSV whose filename ends with _{label}.csv."""
    bench_dir = EE_BENCH_RUNS_DIR if jurisdiction == "ee" else BENCH_RUNS_DIR
    matches = sorted(bench_dir.glob(f"*_{label}.csv"))
    if not matches:
        raise FileNotFoundError(f"No bench run CSV found for label '{label}' in {bench_dir}")
    return matches[-1]


def load_scores(path: Path) -> dict[str, float]:
    """Load {statute_id -> similarity} from one bench run CSV."""
    scores: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or header-less CSV: {path}")
        required = {"statute_id", "similarity"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"CSV {path} missing expected columns: {missing}. Found: {reader.fieldnames}")
        for row in reader:
            sid = row["statute_id"].strip()
            try:
                score = float(row["similarity"])
            except ValueError:
                continue
            scores[sid] = score
    return scores


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_guard(
    baseline_label: str,
    current_label: str,
    threshold: float = 0.005,
    max_regressions: int = 3,
    jurisdiction: str = "fi",
) -> int:
    """Run the regression guard and print a summary. Returns process-style exit code."""
    try:
        baseline_path = find_csv_by_label(baseline_label, jurisdiction)
        current_path = find_csv_by_label(current_label, jurisdiction)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1

    print(f"Baseline : {baseline_path.name}")
    print(f"Current  : {current_path.name}")
    print()

    try:
        baseline_scores = load_scores(baseline_path)
        current_scores = load_scores(current_path)
    except (ValueError, OSError) as exc:
        print(f"ERROR loading CSV: {exc}")
        return 1

    common = sorted(set(baseline_scores) & set(current_scores))
    baseline_only = sorted(set(baseline_scores) - set(current_scores))
    current_only = sorted(set(current_scores) - set(baseline_scores))

    diffs: list[tuple[str, float, float, float]] = []
    for sid in common:
        old = baseline_scores[sid]
        new = current_scores[sid]
        diffs.append((sid, old, new, new - old))

    old_mean = mean([diff[1] for diff in diffs])
    new_mean = mean([diff[2] for diff in diffs])
    agg_delta = new_mean - old_mean

    print(f"Statutes in baseline : {len(baseline_scores)}")
    print(f"Statutes in current  : {len(current_scores)}")
    print(f"Common statutes      : {len(common)}")
    if baseline_only:
        print(f"Baseline-only        : {len(baseline_only)}  (not in current run)")
    if current_only:
        print(f"Current-only         : {len(current_only)}  (new in current run)")
    print()

    sign = "+" if agg_delta >= 0 else ""
    print("Aggregate score (common statutes):")
    print(f"  Baseline mean : {old_mean:.6f}")
    print(f"  Current mean  : {new_mean:.6f}")
    print(f"  Delta         : {sign}{agg_delta:.6f}")
    print()

    regressions = sorted(
        [diff for diff in diffs if diff[3] < -threshold],
        key=lambda item: item[3],
    )
    improvements = sorted(
        [diff for diff in diffs if diff[3] > threshold],
        key=lambda item: -item[3],
    )

    if regressions:
        print(f"Regressions > {threshold:.4f} : {len(regressions)} statute(s)  (max allowed: {max_regressions})")
        print(f"  {'Statute':<20} {'Baseline':>10} {'Current':>10} {'Delta':>10}")
        print(f"  {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 10}")
        for sid, old, new, delta in regressions:
            print(f"  {sid:<20} {old:>10.6f} {new:>10.6f} {delta:>+10.6f}")
        print()
    else:
        print(f"Regressions > {threshold:.4f} : 0  (none)")
        print()

    if improvements:
        print(f"Improvements > {threshold:.4f} : {len(improvements)} statute(s)")
        print(f"  {'Statute':<20} {'Baseline':>10} {'Current':>10} {'Delta':>10}")
        print(f"  {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 10}")
        for sid, old, new, delta in improvements:
            print(f"  {sid:<20} {old:>10.6f} {new:>10.6f} {delta:>+10.6f}")
        print()
    else:
        print(f"Improvements > {threshold:.4f} : 0  (none)")
        print()

    fail = False
    fail_reasons: list[str] = []
    agg_hard_limit = 0.005
    if agg_delta < -agg_hard_limit:
        fail = True
        fail_reasons.append(f"Aggregate score dropped {agg_delta:.6f} (>{agg_hard_limit:.3f} hard limit)")
    if len(regressions) > max_regressions:
        fail = True
        fail_reasons.append(f"{len(regressions)} statutes regressed beyond threshold (max allowed: {max_regressions})")

    if fail:
        print("RESULT: FAIL")
        for reason in fail_reasons:
            print(f"  - {reason}")
        return 1

    print("RESULT: PASS")
    return 0


def main(args: "argparse.Namespace") -> None:
    jurisdiction = getattr(args, "jurisdiction", "fi")
    raise SystemExit(
        run_guard(
            baseline_label=args.baseline,
            current_label=args.current,
            threshold=args.threshold,
            max_regressions=args.max_regressions,
            jurisdiction=jurisdiction,
        )
    )


__all__ = ["BENCH_RUNS_DIR", "EE_BENCH_RUNS_DIR", "find_csv_by_label", "load_scores", "main", "run_guard"]

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
UK_BENCH_RUNS_DIR = _REPO_ROOT / "data" / "uk_bench_runs"


def find_csv_by_label(label: str, jurisdiction: str = "fi") -> Path:
    """Find the most recent run CSV whose filename ends with _{label}.csv."""
    if jurisdiction == "uk":
        direct_path = UK_BENCH_RUNS_DIR / f"{label}.csv"
        if direct_path.exists():
            return direct_path
        bench_dir = UK_BENCH_RUNS_DIR
    elif jurisdiction == "ee":
        bench_dir = EE_BENCH_RUNS_DIR
    else:
        bench_dir = BENCH_RUNS_DIR
    matches = sorted(bench_dir.glob(f"*_{label}.csv"))
    if not matches:
        raise FileNotFoundError(f"No bench run CSV found for label '{label}' in {bench_dir}")
    return matches[-1]


def _load_float_column(path: Path, column: str) -> dict[str, float]:
    """Load {statute_id -> float(row[column])} from one bench run CSV."""
    values: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or header-less CSV: {path}")
        if "statute_id" not in reader.fieldnames:
            raise ValueError(f"CSV {path} missing expected column: statute_id. Found: {reader.fieldnames}")
        if column not in reader.fieldnames:
            raise ValueError(f"CSV {path} missing expected column '{column}'. Found: {reader.fieldnames}")
        for row in reader:
            sid = row["statute_id"].strip()
            try:
                value = float(row[column])
            except ValueError:
                continue
            values[sid] = value
    return values


def _load_first_float_column(
    path: Path,
    columns: tuple[str, ...],
) -> tuple[str, dict[str, float]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or header-less CSV: {path}")
        fieldnames = set(reader.fieldnames)
    for column in columns:
        if column not in fieldnames:
            continue
        values = _load_float_column(path, column)
        if values:
            return column, values
    raise ValueError(
        f"CSV {path} missing expected non-empty score column from {columns}. "
        f"Found: {sorted(fieldnames)}"
    )


def load_scores(path: Path) -> dict[str, float]:
    """Load {statute_id -> similarity} from one bench run CSV."""
    _score_column, scores = _load_first_float_column(path, ("similarity", "score"))
    return scores


def load_durations(path: Path) -> dict[str, float]:
    """Load {statute_id -> duration_s} from one bench run CSV."""
    return _load_float_column(path, "duration_s")


def _parse_float_cell(row: dict[str, str], column: str) -> float | None:
    raw = row.get(column, "")
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _load_uk_scores(path: Path) -> tuple[str, dict[str, float]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or header-less CSV: {path}")
        fieldnames = set(reader.fieldnames)
        if "statute_id" not in fieldnames:
            raise ValueError(f"CSV {path} missing expected column: statute_id. Found: {reader.fieldnames}")
        has_replay_lane = "replay_commencement_score" in fieldnames or "replay_score" in fieldnames
        if not has_replay_lane:
            return _load_first_float_column(path, ("similarity", "score"))

        scores: dict[str, float] = {}
        for row in reader:
            sid = row["statute_id"].strip()
            score = _parse_float_cell(row, "replay_commencement_score")
            if score is None:
                score = _parse_float_cell(row, "replay_score")
            if score is None:
                continue
            scores[sid] = score
    if not scores:
        raise ValueError(
            f"CSV {path} has replay score columns but no parseable replay score values. "
            f"Found: {sorted(fieldnames)}"
        )
    return "uk_replay_primary", scores


def _load_scores_for_jurisdiction(path: Path, jurisdiction: str) -> tuple[str, dict[str, float]]:
    if jurisdiction == "uk":
        return _load_uk_scores(path)
    return _load_first_float_column(path, ("similarity", "score"))


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_guard(
    baseline_label: str,
    current_label: str,
    threshold: float = 0.005,
    max_regressions: int = 3,
    jurisdiction: str = "fi",
    duration_threshold_s: float = 1.0,
    max_duration_regressions: int | None = None,
) -> int:
    """Run the regression guard and print a summary. Returns process-style exit code."""
    if threshold < 0.0:
        print("ERROR: --threshold must be nonnegative")
        return 1
    if max_regressions < 0:
        print("ERROR: --max-regressions must be nonnegative")
        return 1
    if duration_threshold_s < 0.0:
        print("ERROR: --duration-threshold-s must be nonnegative")
        return 1
    if max_duration_regressions is not None and max_duration_regressions < 0:
        print("ERROR: --max-duration-regressions must be nonnegative")
        return 1

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
        baseline_score_column, baseline_scores = _load_scores_for_jurisdiction(
            baseline_path,
            jurisdiction,
        )
        current_score_column, current_scores = _load_scores_for_jurisdiction(
            current_path,
            jurisdiction,
        )
    except (ValueError, OSError) as exc:
        print(f"ERROR loading CSV: {exc}")
        return 1
    if baseline_score_column != current_score_column:
        print(
            "ERROR loading CSV: baseline and current use different score columns "
            f"({baseline_score_column!r} vs {current_score_column!r})"
        )
        return 1

    common = sorted(set(baseline_scores) & set(current_scores))
    baseline_only = sorted(set(baseline_scores) - set(current_scores))
    current_only = sorted(set(current_scores) - set(baseline_scores))

    print(f"Statutes in baseline : {len(baseline_scores)}")
    print(f"Statutes in current  : {len(current_scores)}")
    print(f"Common statutes      : {len(common)}")
    print(f"Score column         : {baseline_score_column}")
    if baseline_only:
        print(f"Baseline-only        : {len(baseline_only)}  (not in current run)")
    if current_only:
        print(f"Current-only         : {len(current_only)}  (new in current run)")
    print()
    if not common:
        print("ERROR: baseline and current have no common scored statutes")
        return 1

    diffs: list[tuple[str, float, float, float]] = []
    for sid in common:
        old = baseline_scores[sid]
        new = current_scores[sid]
        diffs.append((sid, old, new, new - old))

    old_mean = mean([diff[1] for diff in diffs])
    new_mean = mean([diff[2] for diff in diffs])
    agg_delta = new_mean - old_mean

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
    duration_regressions: list[tuple[str, float, float, float]] = []
    if max_duration_regressions is not None:
        try:
            baseline_durations = load_durations(baseline_path)
            current_durations = load_durations(current_path)
        except (ValueError, OSError) as exc:
            print(f"ERROR loading duration CSV data: {exc}")
            return 1
        duration_common = sorted(set(baseline_durations) & set(current_durations))
        if not duration_common:
            print("ERROR: baseline and current have no common duration_s rows")
            return 1
        duration_diffs: list[tuple[str, float, float, float]] = []
        for sid in duration_common:
            old = baseline_durations[sid]
            new = current_durations[sid]
            duration_diffs.append((sid, old, new, new - old))
        duration_regressions = sorted(
            [diff for diff in duration_diffs if diff[3] > duration_threshold_s],
            key=lambda item: item[3],
            reverse=True,
        )
        old_duration_mean = mean([diff[1] for diff in duration_diffs])
        new_duration_mean = mean([diff[2] for diff in duration_diffs])
        duration_delta = new_duration_mean - old_duration_mean
        sign = "+" if duration_delta >= 0 else ""
        print("Aggregate duration_s (common statutes):")
        print(f"  Baseline mean : {old_duration_mean:.3f}s")
        print(f"  Current mean  : {new_duration_mean:.3f}s")
        print(f"  Delta         : {sign}{duration_delta:.3f}s")
        print()
        if duration_regressions:
            print(
                f"Duration regressions > {duration_threshold_s:.3f}s : "
                f"{len(duration_regressions)} statute(s)  "
                f"(max allowed: {max_duration_regressions})"
            )
            print(f"  {'Statute':<20} {'Baseline':>10} {'Current':>10} {'Delta':>10}")
            print(f"  {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 10}")
            for sid, old, new, delta in duration_regressions:
                print(f"  {sid:<20} {old:>9.3f}s {new:>9.3f}s {delta:>+9.3f}s")
            print()
        else:
            print(f"Duration regressions > {duration_threshold_s:.3f}s : 0  (none)")
            print()

    agg_hard_limit = 0.005
    if agg_delta < -agg_hard_limit:
        fail = True
        fail_reasons.append(f"Aggregate score dropped {agg_delta:.6f} (>{agg_hard_limit:.3f} hard limit)")
    if len(regressions) > max_regressions:
        fail = True
        fail_reasons.append(f"{len(regressions)} statutes regressed beyond threshold (max allowed: {max_regressions})")
    if max_duration_regressions is not None and len(duration_regressions) > max_duration_regressions:
        fail = True
        fail_reasons.append(
            f"{len(duration_regressions)} statutes slowed beyond duration threshold "
            f"(max allowed: {max_duration_regressions})"
        )

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
            duration_threshold_s=args.duration_threshold_s,
            max_duration_regressions=args.max_duration_regressions,
        )
    )


__all__ = [
    "BENCH_RUNS_DIR",
    "EE_BENCH_RUNS_DIR",
    "UK_BENCH_RUNS_DIR",
    "find_csv_by_label",
    "load_durations",
    "load_scores",
    "main",
    "run_guard",
]

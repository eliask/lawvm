"""lawvm bench-regression-guard — compare saved bench runs and fail on regressions."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


_REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_RUNS_DIR = _REPO_ROOT / "data" / "bench_runs"
EE_BENCH_RUNS_DIR = _REPO_ROOT / "data" / "ee_bench_runs"
UK_BENCH_RUNS_DIR = _REPO_ROOT / "data" / "uk_bench_runs"
_UK_REPLAY_REGIME_COLUMNS = (
    "uk_metadata_backfill_enabled",
    "uk_oracle_alignment_enabled",
    "uk_metadata_only_effects_enabled",
    "uk_applicability_mode",
    "uk_authority_mode",
)


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


def load_phase_timings(path: Path) -> dict[str, dict[str, float]]:
    """Load persisted per-row phase timings when a bench CSV contains them."""
    rows: dict[str, dict[str, float]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or header-less CSV: {path}")
        if "statute_id" not in reader.fieldnames:
            raise ValueError(f"CSV {path} missing expected column: statute_id. Found: {reader.fieldnames}")
        phase_columns = tuple(
            field
            for field in reader.fieldnames
            if field.startswith("phase_") and field.endswith("_s")
        )
        if not phase_columns:
            return rows
        for row in reader:
            sid = row["statute_id"].strip()
            phases: dict[str, float] = {}
            for column in phase_columns:
                value = _parse_float_cell(row, column)
                if value is None or value <= 0.0:
                    continue
                phase_name = column.removeprefix("phase_").removesuffix("_s")
                phases[phase_name] = value
            if phases:
                rows[sid] = phases
    return rows


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


def _load_uk_replay_regimes(path: Path) -> dict[str, str]:
    """Load per-row UK replay regimes when saved-run columns are present."""
    regimes: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or header-less CSV: {path}")
        fieldnames = set(reader.fieldnames)
        if "statute_id" not in fieldnames:
            raise ValueError(f"CSV {path} missing expected column: statute_id. Found: {reader.fieldnames}")
        if not set(_UK_REPLAY_REGIME_COLUMNS).issubset(fieldnames):
            return regimes
        for row in reader:
            sid = row["statute_id"].strip()
            values = {column: row.get(column, "").strip() for column in _UK_REPLAY_REGIME_COLUMNS}
            if not sid or any(value == "" for value in values.values()):
                continue
            regimes[sid] = (
                f"metadata_backfill={values['uk_metadata_backfill_enabled']};"
                f"oracle_alignment={values['uk_oracle_alignment_enabled']};"
                f"metadata_only_effects={values['uk_metadata_only_effects_enabled']};"
                f"applicability={values['uk_applicability_mode']};"
                f"authority={values['uk_authority_mode']}"
            )
    return regimes


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _phase_row_total(phases: dict[str, float]) -> float:
    total = phases.get("total")
    if total is not None:
        return total
    return sum(value for name, value in phases.items() if name != "total")


def _print_phase_timing_delta_summary(
    baseline_phase_timings: dict[str, dict[str, float]],
    current_phase_timings: dict[str, dict[str, float]],
) -> None:
    common = sorted(set(baseline_phase_timings) & set(current_phase_timings))
    if not common:
        return
    baseline_total = sum(_phase_row_total(baseline_phase_timings[sid]) for sid in common)
    current_total = sum(_phase_row_total(current_phase_timings[sid]) for sid in common)
    phase_totals_old: Counter[str] = Counter()
    phase_totals_new: Counter[str] = Counter()
    for sid in common:
        for name, value in baseline_phase_timings[sid].items():
            if name != "total":
                phase_totals_old[name] += value
        for name, value in current_phase_timings[sid].items():
            if name != "total":
                phase_totals_new[name] += value

    delta = current_total - baseline_total
    sign = "+" if delta >= 0 else ""
    print("Aggregate phase timings (common rows):")
    print(f"  Rows          : {len(common)}")
    print(f"  Baseline total: {baseline_total:.3f}s")
    print(f"  Current total : {current_total:.3f}s")
    print(f"  Delta         : {sign}{delta:.3f}s")
    phase_names = set(phase_totals_old) | set(phase_totals_new)
    if phase_names:
        ranked = sorted(
            phase_names,
            key=lambda name: (
                abs(phase_totals_new.get(name, 0.0) - phase_totals_old.get(name, 0.0)),
                name,
            ),
            reverse=True,
        )
        phase_text = " ".join(
            f"{name}={phase_totals_new.get(name, 0.0) - phase_totals_old.get(name, 0.0):+.3f}s"
            for name in ranked[:8]
        )
        print(f"  Phase deltas  : {phase_text}")
    print()


def _phase_regression_diffs(
    baseline_phase_timings: dict[str, dict[str, float]],
    current_phase_timings: dict[str, dict[str, float]],
    *,
    threshold_s: float,
    phase_filter: tuple[str, ...] = (),
) -> tuple[dict[str, int], list[tuple[str, str, float, float, float]]]:
    common = sorted(set(baseline_phase_timings) & set(current_phase_timings))
    wanted_phases = set(phase_filter)
    regressions: list[tuple[str, str, float, float, float]] = []
    comparable_cells_by_phase: Counter[str] = Counter()
    for sid in common:
        phase_names = (
            set(baseline_phase_timings[sid])
            & set(current_phase_timings[sid])
        ) - {"total"}
        if wanted_phases:
            phase_names &= wanted_phases
        for phase_name in sorted(phase_names):
            old = baseline_phase_timings[sid].get(phase_name, 0.0)
            new = current_phase_timings[sid].get(phase_name, 0.0)
            comparable_cells_by_phase[phase_name] += 1
            delta = new - old
            if delta > threshold_s:
                regressions.append((sid, phase_name, old, new, delta))
    regressions.sort(key=lambda item: item[4], reverse=True)
    return dict(comparable_cells_by_phase), regressions


def run_guard(
    baseline_label: str,
    current_label: str,
    threshold: float = 0.005,
    max_regressions: int = 3,
    jurisdiction: str = "fi",
    duration_threshold_s: float = 1.0,
    max_duration_regressions: int | None = None,
    phase_threshold_s: float = 1.0,
    max_phase_regressions: int | None = None,
    phase_names: tuple[str, ...] = (),
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
    if phase_threshold_s < 0.0:
        print("ERROR: --phase-threshold-s must be nonnegative")
        return 1
    if max_phase_regressions is not None and max_phase_regressions < 0:
        print("ERROR: --max-phase-regressions must be nonnegative")
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

    if jurisdiction == "uk":
        try:
            baseline_regimes = _load_uk_replay_regimes(baseline_path)
            current_regimes = _load_uk_replay_regimes(current_path)
        except (ValueError, OSError) as exc:
            print(f"ERROR loading UK replay regime CSV data: {exc}")
            return 1
        regime_common = sorted(set(baseline_regimes) & set(current_regimes) & set(common))
        if regime_common:
            old_regime_counts = Counter(baseline_regimes[sid] for sid in regime_common)
            new_regime_counts = Counter(current_regimes[sid] for sid in regime_common)
            print("UK replay regimes (common scored rows with regime evidence):")
            print(f"  Baseline: {dict(sorted(old_regime_counts.items()))}")
            print(f"  Current : {dict(sorted(new_regime_counts.items()))}")
            mismatched = [
                (sid, baseline_regimes[sid], current_regimes[sid])
                for sid in regime_common
                if baseline_regimes[sid] != current_regimes[sid]
            ]
            if mismatched:
                print(f"ERROR: UK replay regime mismatch on {len(mismatched)} common scored row(s)")
                for sid, old, new in mismatched[:10]:
                    print(f"  {sid}: {old} -> {new}")
                if len(mismatched) > 10:
                    print(f"  ... {len(mismatched) - 10} more")
                return 1
            print()
        elif baseline_regimes or current_regimes:
            print("WARNING: no common UK replay regime evidence rows; comparing score lane only")
            print()

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

    try:
        _print_phase_timing_delta_summary(
            load_phase_timings(baseline_path),
            load_phase_timings(current_path),
        )
    except (ValueError, OSError) as exc:
        print(f"WARNING: could not load optional phase timing CSV data: {exc}")
        print()

    fail = False
    fail_reasons: list[str] = []
    duration_regressions: list[tuple[str, float, float, float]] = []
    phase_regressions: list[tuple[str, str, float, float, float]] = []
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
    if max_phase_regressions is not None:
        try:
            baseline_phase_timings = load_phase_timings(baseline_path)
            current_phase_timings = load_phase_timings(current_path)
        except (ValueError, OSError) as exc:
            print(f"ERROR loading phase timing CSV data: {exc}")
            return 1
        phase_common = sorted(set(baseline_phase_timings) & set(current_phase_timings))
        if not phase_common:
            print("ERROR: baseline and current have no common phase timing rows")
            return 1
        comparable_cells_by_phase, phase_regressions = _phase_regression_diffs(
            baseline_phase_timings,
            current_phase_timings,
            threshold_s=phase_threshold_s,
            phase_filter=phase_names,
        )
        missing_selected_phases = tuple(
            phase_name
            for phase_name in phase_names
            if comparable_cells_by_phase.get(phase_name, 0) == 0
        )
        if missing_selected_phases:
            selected = ", ".join(missing_selected_phases)
            print(
                "ERROR: baseline and current have no comparable timing cells "
                f"for selected phase(s): {selected}"
            )
            return 1
        if not comparable_cells_by_phase:
            print("ERROR: baseline and current have no comparable non-total phase timing cells")
            return 1
        if phase_regressions:
            phase_scope = f" for phase(s) {', '.join(phase_names)}" if phase_names else ""
            print(
                f"Phase regressions{phase_scope} > {phase_threshold_s:.3f}s : "
                f"{len(phase_regressions)} row/phase cell(s)  "
                f"(max allowed: {max_phase_regressions})"
            )
            print(f"  {'Statute':<20} {'Phase':<16} {'Baseline':>10} {'Current':>10} {'Delta':>10}")
            print(f"  {'-' * 20} {'-' * 16} {'-' * 10} {'-' * 10} {'-' * 10}")
            for sid, phase_name, old, new, delta in phase_regressions:
                print(f"  {sid:<20} {phase_name:<16} {old:>9.3f}s {new:>9.3f}s {delta:>+9.3f}s")
            print()
        else:
            phase_scope = f" for phase(s) {', '.join(phase_names)}" if phase_names else ""
            print(f"Phase regressions{phase_scope} > {phase_threshold_s:.3f}s : 0  (none)")
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
    if max_phase_regressions is not None and len(phase_regressions) > max_phase_regressions:
        fail = True
        fail_reasons.append(
            f"{len(phase_regressions)} phase cells slowed beyond phase threshold "
            f"(max allowed: {max_phase_regressions})"
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
            phase_threshold_s=args.phase_threshold_s,
            max_phase_regressions=args.max_phase_regressions,
            phase_names=tuple(args.phase_names or ()),
        )
    )


__all__ = [
    "BENCH_RUNS_DIR",
    "EE_BENCH_RUNS_DIR",
    "UK_BENCH_RUNS_DIR",
    "find_csv_by_label",
    "load_durations",
    "load_phase_timings",
    "load_scores",
    "main",
    "run_guard",
]

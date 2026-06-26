"""Jurisdiction-agnostic benchmark aggregation, history, and regression guard.

Lifts the Finland bench's distribution-bucket / history / regression machinery
into a shared core layer that operates on :class:`BenchUnitResult`s. The bucket
semantics are preserved exactly (computed over headline *accuracy*, i.e.
``1 - headline_error``), so a jurisdiction migrating onto this layer keeps its
existing headline numbers.

See ``notes/UNIFIED_BENCH_CONTRACT.md``.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from lawvm.core.bench_contract import (
    BenchStatus,
    BenchUnitResult,
    NON_SCORED_STATUSES,
    check_residue_reconciliation,
)


@dataclass(frozen=True, slots=True)
class BenchDistribution:
    """Distribution buckets over scored units' headline accuracy.

    Mirrors the Finland bench's ``_compute_stats`` buckets exactly so migrated
    benches keep their published numbers. ``mean`` is the mean headline accuracy
    over scored units (``0`` = no scored units).
    """

    mean: float
    n: int
    perfect: int
    above_99: int
    above_95: int
    below_90: int
    errors: int  # non-scored + crashed (i.e. units with no headline accuracy)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "mean": self.mean,
            "n": self.n,
            "perfect": self.perfect,
            "above_99": self.above_99,
            "above_95": self.above_95,
            "below_90": self.below_90,
            "errors": self.errors,
        }


def _scored_accuracies(results: Sequence[BenchUnitResult]) -> list[float]:
    accuracies: list[float] = []
    for r in results:
        acc = r.headline_accuracy()
        if acc is not None:
            accuracies.append(acc)
    return accuracies


def compute_distribution(results: Sequence[BenchUnitResult]) -> BenchDistribution:
    """Distribution buckets over the scored units in *results*.

    A unit contributes to the buckets iff it has a headline accuracy (i.e. it is
    ``SCORED`` and attempted at least one axis). Every other unit counts toward
    ``errors`` — identical to the FI bench treating ``sim < 0`` rows as errors.
    """
    accuracies = _scored_accuracies(results)
    n = len(accuracies)
    errors = len(results) - n
    if n == 0:
        return BenchDistribution(
            mean=0.0, n=0, perfect=0, above_99=0, above_95=0, below_90=0, errors=errors
        )
    return BenchDistribution(
        mean=sum(accuracies) / n,
        n=n,
        perfect=sum(1 for a in accuracies if a >= 0.9999),
        above_99=sum(1 for a in accuracies if a >= 0.99),
        above_95=sum(1 for a in accuracies if a >= 0.95),
        below_90=sum(1 for a in accuracies if a < 0.90),
        errors=errors,
    )


def partition_by_status(
    results: Sequence[BenchUnitResult],
) -> tuple[list[BenchUnitResult], list[BenchUnitResult], list[BenchUnitResult]]:
    """Split *results* into ``(scored, non_scored, crashed)``.

    ``non_scored`` are the excluded-but-not-failed statuses; ``crashed`` are
    genuine failures. This is the uniform replacement for FI's ad-hoc split of
    ``sim < 0`` rows into ``_NONSCORED_STATUSES`` vs the rest.
    """
    scored: list[BenchUnitResult] = []
    non_scored: list[BenchUnitResult] = []
    crashed: list[BenchUnitResult] = []
    for r in results:
        if r.bench_unit_status is BenchStatus.SCORED:
            scored.append(r)
        elif r.bench_unit_status in NON_SCORED_STATUSES:
            non_scored.append(r)
        else:
            crashed.append(r)
    return scored, non_scored, crashed


def format_error_pct(accuracy: float | None) -> str:
    """Display framing: ``(1 - accuracy) * 100`` as a percentage, or ``n/a``."""
    if accuracy is None:
        return "n/a"
    return f"{(1 - accuracy) * 100:.2f}%"


def aggregate_residue_buckets(
    results: Iterable[BenchUnitResult],
) -> Counter[str]:
    """Sum typed residue families across *results* (scored units only)."""
    totals: Counter[str] = Counter()
    for r in results:
        if r.bench_unit_status is not BenchStatus.SCORED:
            continue
        for family, count in r.residue_buckets.items():
            totals[family] += int(count)
    return totals


def check_all_reconcile(results: Iterable[BenchUnitResult]) -> list[str]:
    """Return reconciliation-violation messages for every offending scored unit.

    Empty list ⇒ every scored unit's structural error is explained by its typed
    residue. Benches should assert this is empty (fail loud on silent error).
    """
    from lawvm.core.bench_contract import residue_reconciliation_violation

    violations: list[str] = []
    for r in results:
        msg = residue_reconciliation_violation(r)
        if msg is not None:
            violations.append(msg)
    return violations


def assert_all_reconcile(results: Iterable[BenchUnitResult]) -> None:
    """Raise if any scored unit fails residue reconciliation."""
    results = list(results)
    for r in results:
        check_residue_reconciliation(r)


# ---------------------------------------------------------------------------
# History + regression guard (jurisdiction-agnostic)
# ---------------------------------------------------------------------------

HISTORY_HEADER = [
    "timestamp",
    "label",
    "mean_score",
    "n_statutes",
    "n_perfect",
    "n_above_99",
    "n_above_95",
    "n_below_90",
]


def append_history(
    history_path: Path,
    timestamp: str,
    label: str,
    distribution: BenchDistribution,
) -> None:
    """Append one run summary row to *history_path* (creates header if new).

    Byte-compatible with the FI ``benchmark_history.csv`` schema so existing
    history files and readers keep working.
    """
    write_header = not history_path.exists()
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(HISTORY_HEADER)
        w.writerow(
            [
                timestamp,
                label,
                f"{distribution.mean:.4f}",
                distribution.n,
                distribution.perfect,
                distribution.above_99,
                distribution.above_95,
                distribution.below_90,
            ]
        )


def load_history(history_path: Path) -> list[dict[str, str]]:
    """Load a history CSV as a list of dict rows (empty if absent)."""
    if not history_path.exists():
        return []
    with open(history_path, newline="") as f:
        return list(csv.DictReader(f))


def render_summary(
    results: Sequence[BenchUnitResult],
    label: str,
    *,
    jurisdiction: str = "",
) -> list[str]:
    """Render the shared, jurisdiction-agnostic benchmark summary as text lines.

    Lifts the Finland bench's ``_show_summary`` shape into core so every
    jurisdiction prints the same headline framing: scored / non-scored / crashed
    counts, worst-of mean error, the distribution buckets, and a
    residue-reconciliation line (the honesty property). Returns lines so callers
    can print or test them.
    """
    scored, non_scored, crashed = partition_by_status(results)
    dist = compute_distribution(results)
    violations = check_all_reconcile(results)
    header = "=== UNIFIED BENCH SUMMARY"
    if jurisdiction:
        header += f"  jurisdiction={jurisdiction}"
    header += f"  label={label} ==="
    lines = [
        header,
        f"  Units      : {dist.n} scored  crashed: {len(crashed)}  "
        f"excluded(non-scored): {len(non_scored)}",
        f"  Mean error : {format_error_pct(dist.mean if dist.n else None)}  (worst-of axes)",
        f"  Perfect  : {dist.perfect}  >=99%: {dist.above_99}  "
        f">=95%: {dist.above_95}  <90%: {dist.below_90}",
    ]
    if violations:
        lines.append(
            f"  RESIDUE RECONCILIATION: {len(violations)} VIOLATION(S) — "
            "structural error not explained by typed residue (fail loud):"
        )
        for msg in violations[:5]:
            lines.append(f"    - {msg}")
    else:
        lines.append(
            "  Residue reconciliation: OK "
            "(every scored unit's structural error is explained by typed residue)"
        )
    return lines


@dataclass(frozen=True, slots=True)
class Regression:
    unit_id: str
    previous_accuracy: float
    current_accuracy: float

    @property
    def delta(self) -> float:
        return self.current_accuracy - self.previous_accuracy


def find_regressions(
    previous: Mapping[str, float],
    current: Mapping[str, float],
    *,
    tolerance: float = 0.001,
) -> list[Regression]:
    """Units present in both runs whose accuracy dropped by more than *tolerance*.

    *previous* / *current* map ``unit_id -> headline accuracy``. Returned list is
    sorted worst-regression-first (most negative delta).
    """
    regressions: list[Regression] = []
    for unit_id, prev_acc in previous.items():
        if unit_id not in current:
            continue
        curr_acc = current[unit_id]
        if curr_acc - prev_acc < -tolerance:
            regressions.append(Regression(unit_id, prev_acc, curr_acc))
    regressions.sort(key=lambda r: r.delta)
    return regressions

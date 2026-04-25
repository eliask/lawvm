"""lawvm ee-frontier — rank EE bench rows by open vs adjudicated residuals."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml, open_rt_archive
from lawvm.estonia.pair_planning import plan_ee_oracle_pair
from lawvm.tools.ee_bench import _BENCH_DIR
from lawvm.tools.ee_reporting import (
    EEBenchmarkReportingStratum,
    build_ee_benchmark_reporting_summary,
    build_ee_comparison_policy_summary,
    count_ee_benchmark_reporting_strata,
)

if TYPE_CHECKING:
    import argparse


@dataclass(frozen=True)
class EEFrontierRow:
    """One EE bench row enriched for frontier ranking."""

    grupi_id: str
    base_id: str
    oracle_id: str
    title: str
    n_ops: int
    n_divs: int
    sec_match: float
    status: str
    comparison_class: str
    source_basis: str
    core_benchmark: bool
    benchmark_reporting_stratum: str
    benchmark_reporting_headline_eligible: bool
    adjudicated_residual_count: int
    matched_current_residual_count: int
    adjudicated_bucket_counts: str
    unknown_current_residual_count: int
    open_current_divergence_count: int
    frontier_bucket: str


def _resolve_run_path(label_or_path: str | None) -> Path:
    if label_or_path:
        candidate = Path(label_or_path)
        if candidate.exists():
            return candidate
        matches = sorted(_BENCH_DIR.glob(f"*{label_or_path}*.csv"))
        if matches:
            return matches[-1]
        direct = _BENCH_DIR / f"{label_or_path}.csv"
        if direct.exists():
            return direct
        raise FileNotFoundError(f"EE bench run not found for label/path: {label_or_path}")

    matches = sorted(_BENCH_DIR.glob("*.csv"))
    if not matches:
        raise FileNotFoundError(f"No EE bench runs found in {_BENCH_DIR}")
    return matches[-1]


def _to_int(raw: str | None, default: int = 0) -> int:
    try:
        return int(raw or default)
    except ValueError:
        return default


def _to_float(raw: str | None, default: float = 0.0) -> float:
    try:
        return float(raw or default)
    except ValueError:
        return default


def _resolve_source_basis(row: EEFrontierRow, archive) -> str:
    """Derive the EE source basis for reporting from the current archive."""
    if row.source_basis:
        return row.source_basis
    if not row.base_id or not row.oracle_id:
        return ""
    try:
        base_xml = fetch_rt_xml(row.base_id, archive)
        oracle_xml = fetch_rt_xml(row.oracle_id, archive)
        planning = plan_ee_oracle_pair(
            base_id=row.base_id,
            as_of=extract_effective_date(oracle_xml) or "2026-03-24",
            base_xml=base_xml,
            archive=archive,
            oracle_id=row.oracle_id,
        )
        return planning.plan.source_basis.value
    except Exception:
        return ""


def _open_frontier_rank(row: EEFrontierRow) -> tuple[int, int, int, int, float, int]:
    """Prefer core, commensurable, source-backed rows over editorial/non-core noise."""
    comparison = row.comparison_class or ""
    core_rank = 0 if row.core_benchmark else 1
    comparison_rank = 0 if comparison == "commensurable_delta" else 1
    editorial_rank = 1 if comparison == "same_chain_editorial_drift" else 0
    zero_ops_rank = 1 if row.n_ops == 0 else 0
    return (
        core_rank,
        comparison_rank,
        editorial_rank,
        zero_ops_rank,
        row.sec_match,
        -row.open_current_divergence_count,
    )


def _load_rows(path: Path) -> list[EEFrontierRow]:
    rows: list[EEFrontierRow] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n_divs = _to_int(row.get("n_divs"))
            matched = _to_int(row.get("matched_current_residual_count"))
            adjudicated = _to_int(row.get("adjudicated_residual_count"))
            unknown_current = _to_int(row.get("unknown_current_residual_count"))
            open_current_raw = row.get("open_current_divergence_count")
            if open_current_raw is None or open_current_raw == "":
                if matched or adjudicated or unknown_current:
                    open_current = max(unknown_current, n_divs - matched)
                else:
                    # Legacy EE runs had no residual classification fields.
                    # Treat all remaining divergences as still-open frontier work.
                    open_current = n_divs
            else:
                open_current = _to_int(open_current_raw)
                if n_divs > 0 and matched == 0 and adjudicated == 0 and unknown_current == 0 and open_current == 0:
                    # Some transitional EE runs wrote the new residual columns but
                    # still left uninventoried non-zero rows at zero-open. Treat
                    # those rows as active frontier work, not adjudicated/legacy.
                    open_current = n_divs

            if n_divs == 0:
                bucket = "resolved"
            elif open_current > 0:
                bucket = "open"
            elif matched > 0 or adjudicated > 0:
                bucket = "adjudicated_nonzero"
            else:
                bucket = "legacy_unclassified_nonzero"

            rows.append(
                EEFrontierRow(
                    grupi_id=row.get("grupi_id", ""),
                    base_id=row.get("base_id", ""),
                    oracle_id=row.get("oracle_id", ""),
                    title=row.get("title", ""),
                    n_ops=_to_int(row.get("n_ops")),
                    n_divs=n_divs,
                    sec_match=_to_float(row.get("sec_match")),
                    status=row.get("status", ""),
                    comparison_class=row.get("comparison_class", ""),
                    source_basis=row.get("source_basis", ""),
                    core_benchmark=row.get("core_benchmark", "1") in ("1", "True", "true"),
                    benchmark_reporting_stratum="",
                    benchmark_reporting_headline_eligible=False,
                    adjudicated_residual_count=adjudicated,
                    matched_current_residual_count=matched,
                    adjudicated_bucket_counts=row.get("adjudicated_bucket_counts", ""),
                    unknown_current_residual_count=unknown_current,
                    open_current_divergence_count=open_current,
                    frontier_bucket=bucket,
                )
            )
    return rows


def build_frontier_payload(
    label_or_path: str | None = None,
    *,
    top: int = 20,
    include_adjudicated: bool = False,
) -> dict:
    path = _resolve_run_path(label_or_path)
    rows = _load_rows(path)
    needs_source_basis = any(not row.source_basis for row in rows)
    archive = None
    if needs_source_basis:
        try:
            archive = open_rt_archive(readonly=True)
        except Exception:
            archive = None
    comparison_policy = build_ee_comparison_policy_summary()
    try:
        resolved_rows: list[EEFrontierRow] = []
        for row in rows:
            source_basis = row.source_basis
            if not source_basis and archive is not None:
                source_basis = _resolve_source_basis(row, archive)
            reporting_summary = build_ee_benchmark_reporting_summary(
                source_basis,
                row.comparison_class,
            )
            resolved_rows.append(
                EEFrontierRow(
                    grupi_id=row.grupi_id,
                    base_id=row.base_id,
                    oracle_id=row.oracle_id,
                    title=row.title,
                    n_ops=row.n_ops,
                    n_divs=row.n_divs,
                    sec_match=row.sec_match,
                    status=row.status,
                    comparison_class=row.comparison_class,
                    source_basis=source_basis,
                    core_benchmark=row.core_benchmark,
                    benchmark_reporting_stratum=reporting_summary["benchmark_reporting_stratum"],
                    benchmark_reporting_headline_eligible=reporting_summary[
                        "benchmark_reporting_headline_eligible"
                    ],
                    adjudicated_residual_count=row.adjudicated_residual_count,
                    matched_current_residual_count=row.matched_current_residual_count,
                    adjudicated_bucket_counts=row.adjudicated_bucket_counts,
                    unknown_current_residual_count=row.unknown_current_residual_count,
                    open_current_divergence_count=row.open_current_divergence_count,
                    frontier_bucket=row.frontier_bucket,
                )
            )
        rows = resolved_rows

        reporting_counts = count_ee_benchmark_reporting_strata(
            (row.source_basis, row.comparison_class) for row in rows
        )
        headline_row_count = reporting_counts[EEBenchmarkReportingStratum.CORE_COMMENSURABLE.value]

        open_rows = [
            row for row in rows
            if row.status == "OK" and row.frontier_bucket == "open"
        ]
        open_rows.sort(key=_open_frontier_rank)
        open_headline_rows = [
            row for row in open_rows
            if row.benchmark_reporting_headline_eligible
        ]
        open_nonheadline_rows = [
            row for row in open_rows
            if not row.benchmark_reporting_headline_eligible
        ]

        adjudicated_rows = [
            row for row in rows
            if row.status == "OK" and row.frontier_bucket == "adjudicated_nonzero"
        ]
        adjudicated_rows.sort(key=lambda row: (row.sec_match, -row.n_divs))

        legacy_rows = [
            row for row in rows
            if row.status == "OK" and row.frontier_bucket == "legacy_unclassified_nonzero"
        ]
        legacy_rows.sort(key=lambda row: (row.sec_match, -row.n_divs))

        selected = list(open_rows[:top])
        if include_adjudicated:
            selected.extend(adjudicated_rows[:top])

        return {
            "run_path": str(path),
            "total_rows": len(rows),
            "comparison_policy": comparison_policy,
            "benchmark_reporting_strata_counts": reporting_counts,
            "benchmark_reporting_headline_row_count": headline_row_count,
            "open_row_count": len(open_rows),
            "open_headline_row_count": len(open_headline_rows),
            "open_nonheadline_row_count": len(open_nonheadline_rows),
            "adjudicated_nonzero_row_count": len(adjudicated_rows),
            "legacy_unclassified_nonzero_row_count": len(legacy_rows),
            "rows": [
                {
                    "grupi_id": row.grupi_id,
                    "base_id": row.base_id,
                    "oracle_id": row.oracle_id,
                    "title": row.title,
                    "n_ops": row.n_ops,
                    "n_divs": row.n_divs,
                    "sec_match": row.sec_match,
                    "comparison_class": row.comparison_class,
                    "source_basis": row.source_basis,
                    "benchmark_reporting_stratum": row.benchmark_reporting_stratum,
                    "benchmark_reporting_headline_eligible": row.benchmark_reporting_headline_eligible,
                    "core_benchmark": row.core_benchmark,
                    "frontier_bucket": row.frontier_bucket,
                    "matched_current_residual_count": row.matched_current_residual_count,
                    "open_current_divergence_count": row.open_current_divergence_count,
                    "adjudicated_bucket_counts": row.adjudicated_bucket_counts,
                }
                for row in selected
            ],
            "adjudicated_rows": [
                {
                    "base_id": row.base_id,
                    "oracle_id": row.oracle_id,
                    "title": row.title,
                    "n_divs": row.n_divs,
                    "comparison_class": row.comparison_class,
                    "source_basis": row.source_basis,
                    "benchmark_reporting_stratum": row.benchmark_reporting_stratum,
                    "benchmark_reporting_headline_eligible": row.benchmark_reporting_headline_eligible,
                    "matched_current_residual_count": row.matched_current_residual_count,
                    "adjudicated_bucket_counts": row.adjudicated_bucket_counts,
                }
                for row in adjudicated_rows[:top]
            ],
            "legacy_rows": [
                {
                    "base_id": row.base_id,
                    "oracle_id": row.oracle_id,
                    "title": row.title,
                    "n_divs": row.n_divs,
                    "comparison_class": row.comparison_class,
                    "source_basis": row.source_basis,
                    "benchmark_reporting_stratum": row.benchmark_reporting_stratum,
                    "benchmark_reporting_headline_eligible": row.benchmark_reporting_headline_eligible,
                }
                for row in legacy_rows[:top]
            ],
        }
    finally:
        close = getattr(archive, "close", None) if archive is not None else None
        if callable(close):
            close()


def main(args: "argparse.Namespace") -> None:
    payload = build_frontier_payload(
        getattr(args, "label", None),
        top=int(getattr(args, "top", 20) or 20),
        include_adjudicated=bool(getattr(args, "include_adjudicated", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print()
    print("=== EE Frontier ===")
    print(f"  run       : {payload['run_path']}")
    print(f"  open rows : {payload['open_row_count']}")
    print(
        f"    headline-eligible : {payload.get('open_headline_row_count', 0)}"
    )
    print(
        f"    non-headline      : {payload.get('open_nonheadline_row_count', 0)}"
    )
    print(f"  adjudicated non-zero rows : {payload['adjudicated_nonzero_row_count']}")
    print(f"  legacy unclassified rows  : {payload['legacy_unclassified_nonzero_row_count']}")
    comparison_policy = payload.get("comparison_policy", {}) or {}
    if comparison_policy:
        print(
            f"  drift     : {comparison_policy.get('non_silent_rule_count', 0)} non-silent comparison rules"
        )
        class_counts = comparison_policy.get("non_silent_rule_counts_by_class", {})
        if class_counts:
            counts = ", ".join(f"{klass}={count}" for klass, count in class_counts.items())
            print(f"    classes  : {counts}")
        rule_names = comparison_policy.get("non_silent_rule_names", [])
        if rule_names:
            print(f"    rules    : {', '.join(rule_names)}")
    reporting_counts = payload.get("benchmark_reporting_strata_counts", {}) or {}
    if reporting_counts:
        counts = ", ".join(
            f"{stratum}={count}" for stratum, count in reporting_counts.items() if count
        )
        if counts:
            print(f"  reporting : {counts}")

    if payload["rows"]:
        print("\nActive frontier rows:")
        for row in payload["rows"]:
            print(
                f"  {row['base_id']} {row['title'][:35]:35s} "
                f"open={row['open_current_divergence_count']:>3} "
                f"divs={row['n_divs']:>3} sec={row['sec_match']:.1%} "
                f"class={row['comparison_class'] or '(none)'} "
                f"basis={row['source_basis'] or '(none)'} "
                f"stratum={row['benchmark_reporting_stratum'] or '(none)'} "
                f"bucket={row['frontier_bucket']}"
            )
    else:
        print("\nActive frontier rows:")
        print("  (none)")

    if payload["adjudicated_rows"]:
        print("\nAdjudicated non-zero rows:")
        for row in payload["adjudicated_rows"]:
            print(
                f"  {row['base_id']} {row['title'][:35]:35s} "
                f"matched={row['matched_current_residual_count']:>3} "
                f"divs={row['n_divs']:>3} "
                f"class={row['comparison_class'] or '(none)'} "
                f"basis={row['source_basis'] or '(none)'} "
                f"stratum={row['benchmark_reporting_stratum'] or '(none)'} "
                f"buckets={row['adjudicated_bucket_counts']}"
            )

    if payload["legacy_rows"]:
        print("\nLegacy unclassified non-zero rows:")
        for row in payload["legacy_rows"]:
            print(
                f"  {row['base_id']} {row['title'][:35]:35s} "
                f"divs={row['n_divs']:>3} "
                f"class={row['comparison_class'] or '(none)'} "
                f"basis={row['source_basis'] or '(none)'} "
                f"stratum={row['benchmark_reporting_stratum'] or '(none)'}"
            )


__all__ = ["EEFrontierRow", "build_frontier_payload", "main"]

"""U.S. federal dry-run evaluation harness over a committed bench corpus.

This turns "US coverage" into a tracked, witness-anchored number across many
(title, before_year, after_year) USC edition windows. For each window the
harness:

1. **Derives the window laws** from the *witness delta* — the set of Public Laws
   whose ``source-credit`` first appears in the AFTER edition (present in the
   after edition's section credits, absent from the before edition's). This is a
   fact of the two editions, not a hand-curated list, so the corpus cannot drift
   from the sources. The derived ``plaw_locators`` feed the kernel.
2. **Runs the kernel** via :func:`lawvm.us_federal.dry_run.build_us_dry_run_from_archive`
   (this module never re-implements lowering or comparison — it only orchestrates
   and aggregates).
3. **Aggregates** the witness-anchored coverage (agreements / oracle-changed
   sections — the monotone north-star denominator is a fact of the editions) and
   the typed residual-disposition breakdown.

The denominator is ALWAYS the oracle changed-section count. "Covered" means a
section materialized in agreement with the oracle after-text; it never folds in a
``sunset_reversion`` (the temporal layer explains the change but does not
materialize it from source) or an ``oracle_suspect`` (an editorial pathology on
the oracle side). Those are reported as distinct typed partitions.

Runnable without the global CLI::

    python -m lawvm.us_federal.bench
    python -m lawvm.us_federal.bench --corpus us/bench/us_bench_corpus.csv --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from lawvm.us_federal.dry_run import (
    USDryRunReport,
    USDryRunWindowError,
    build_us_dry_run_from_archive,
)
from lawvm.us_federal.source_tree import parse_usc_title_document
from lawvm.us_federal.sources import (
    UsArchiveReader,
    open_us_federal_farchive,
    plaw_locator,
    read_usc_annual,
)
from lawvm.us_federal.usc_witness import extract_title_witnesses

# Default committed corpus (relative to the repo root).
DEFAULT_CORPUS_PATH = Path("us/bench/us_bench_corpus.csv")

# A window the corpus marks ``include=true`` but whose source editions are not in
# the archive: recorded as a typed skip rather than silently dropped.
US_BENCH_WINDOW_EDITION_MISSING_RULE_ID = "us_bench_window_edition_not_in_archive"
# A window whose derived witness delta is empty (no public law to lower): recorded
# so an "empty" window is never mistaken for "ran and found nothing".
US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID = "us_bench_window_witness_delta_empty"


@dataclass(frozen=True)
class BenchWindow:
    """One corpus row: a (title, before_year, after_year) window to evaluate."""

    title: int
    before_year: int
    after_year: int
    include: bool
    window_law_count: int
    prior_edition_years: tuple[int, ...]
    note: str

    @property
    def key(self) -> str:
        return f"title{self.title}:{self.before_year}->{self.after_year}"


@dataclass
class WindowResult:
    """The per-window evaluation outcome (or a typed skip)."""

    window: BenchWindow
    status: str  # "evaluated" | "skipped"
    skip_rule_id: str = ""
    derived_window_laws: tuple[str, ...] = ()
    report: USDryRunReport | None = None

    # Aggregation-ready scalars (populated when status == "evaluated").
    oracle_changed: int = 0
    agreements: int = 0
    lawvm_wrong: int = 0
    oracle_suspect: int = 0
    missing_source: int = 0
    sunset_reversion: int = 0
    refusals: int = 0
    coverage_fraction: float | None = None

    def to_jsonable(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "window": self.window.key,
            "title": self.window.title,
            "before_year": self.window.before_year,
            "after_year": self.window.after_year,
            "status": self.status,
        }
        if self.status == "skipped":
            payload["skip_rule_id"] = self.skip_rule_id
            payload["derived_window_law_count"] = len(self.derived_window_laws)
            return payload
        payload.update(
            {
                "derived_window_laws": list(self.derived_window_laws),
                "oracle_changed_section_count": self.oracle_changed,
                "agreements": self.agreements,
                "coverage_fraction": self.coverage_fraction,
                "lawvm_wrong": self.lawvm_wrong,
                "oracle_suspect": self.oracle_suspect,
                "missing_source": self.missing_source,
                "sunset_reversion": self.sunset_reversion,
                "refusals": self.refusals,
            }
        )
        return payload


@dataclass
class BenchReport:
    """Aggregate across the whole bench corpus."""

    corpus_path: str
    results: list[WindowResult] = field(default_factory=list)

    def evaluated(self) -> list[WindowResult]:
        return [r for r in self.results if r.status == "evaluated"]

    def skipped(self) -> list[WindowResult]:
        return [r for r in self.results if r.status == "skipped"]

    def aggregate(self) -> dict[str, Any]:
        ev = self.evaluated()
        denom = sum(r.oracle_changed for r in ev)
        numer = sum(r.agreements for r in ev)
        return {
            "windows_evaluated": len(ev),
            "windows_skipped": len(self.skipped()),
            "oracle_changed_section_total": denom,
            "agreements_total": numer,
            # Witness-anchored, monotone: agreements / oracle-changed sections.
            "coverage_fraction": (numer / denom) if denom else None,
            "disposition_breakdown": {
                "agreement": numer,
                "lawvm_wrong": sum(r.lawvm_wrong for r in ev),
                "oracle_suspect": sum(r.oracle_suspect for r in ev),
                "missing_source": sum(r.missing_source for r in ev),
                "sunset_reversion": sum(r.sunset_reversion for r in ev),
            },
            "refusals_total": sum(r.refusals for r in ev),
        }

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "jurisdiction": "us_federal",
            "report_kind": "dry_run_bench",
            "truth_claim": "witness_anchored_dry_run_coverage_not_actual_replay",
            "replay_authorized": False,
            "corpus": self.corpus_path,
            "aggregate": self.aggregate(),
            "windows": [r.to_jsonable() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


def _parse_years(field_value: str) -> tuple[int, ...]:
    tokens = field_value.replace(",", " ").split()
    return tuple(int(t) for t in tokens if t)


def load_corpus(path: Path) -> list[BenchWindow]:
    """Parse the committed CSV corpus into typed :class:`BenchWindow` rows.

    Lines beginning with ``#`` are comments; the first non-comment line is the
    header. ``prior_edition_years`` is a space/comma-separated list (may be
    empty). ``note`` may contain anything except a comma (the format is a simple
    comment-aware CSV, not full RFC-4180 — the corpus is ours).
    """
    windows: list[BenchWindow] = []
    header_seen = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not header_seen:
            header_seen = True
            continue
        parts = line.split(",", 6)
        if len(parts) < 6:
            raise ValueError(f"malformed corpus row (need >=6 fields): {raw!r}")
        title, before_year, after_year, include, law_count, prior = parts[:6]
        note = parts[6] if len(parts) > 6 else ""
        windows.append(
            BenchWindow(
                title=int(title),
                before_year=int(before_year),
                after_year=int(after_year),
                include=include.strip().lower() in ("true", "1", "yes"),
                window_law_count=int(law_count),
                prior_edition_years=_parse_years(prior),
                note=note.strip(),
            )
        )
    return windows


# ---------------------------------------------------------------------------
# Witness-delta window-law derivation
# ---------------------------------------------------------------------------


def derive_window_law_locators(
    archive: UsArchiveReader, *, title: int, before_year: int, after_year: int
) -> dict[str, str] | None:
    """Derive ``{statute_id: locator}`` from the editions' witness delta.

    The window laws are the public laws whose ``source-credit`` first appears in
    the after edition (present in the after edition's section credits, absent from
    the before edition's). Returns ``None`` when either edition is missing from
    the archive (the caller turns that into a typed skip). An empty dict is a
    valid result: the after edition credits no new public law.
    """
    before = read_usc_annual(archive, before_year, title)
    after = read_usc_annual(archive, after_year, title)
    if before is None or after is None:
        return None
    before_report = extract_title_witnesses(
        parse_usc_title_document(before, title=title, year=str(before_year))
    )
    after_report = extract_title_witnesses(
        parse_usc_title_document(after, title=title, year=str(after_year))
    )
    new_laws = after_report.distinct_public_laws() - before_report.distinct_public_laws()
    return {
        f"PL {congress}-{number}": plaw_locator(congress, number)
        for congress, number in sorted(new_laws)
    }


# ---------------------------------------------------------------------------
# Per-window evaluation
# ---------------------------------------------------------------------------


def evaluate_window(archive: UsArchiveReader, window: BenchWindow) -> WindowResult:
    """Run the dry-run kernel for one window and project it into a bench row.

    A window whose editions are missing, or whose derived witness delta is empty,
    is a typed skip — never a silently-zero "evaluated" window. A missing PL blob
    surfaces as a :class:`USDryRunWindowError` (the kernel refuses a partial
    window); it is recorded as the same edition-missing skip family so the bench
    never runs a partial window.
    """
    locators = derive_window_law_locators(
        archive,
        title=window.title,
        before_year=window.before_year,
        after_year=window.after_year,
    )
    if locators is None:
        return WindowResult(
            window=window,
            status="skipped",
            skip_rule_id=US_BENCH_WINDOW_EDITION_MISSING_RULE_ID,
        )
    if not locators:
        return WindowResult(
            window=window,
            status="skipped",
            skip_rule_id=US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID,
        )

    try:
        report = build_us_dry_run_from_archive(
            archive,
            title=window.title,
            before_year=window.before_year,
            after_year=window.after_year,
            plaw_locators=locators,
            prior_edition_years=window.prior_edition_years,
        )
    except USDryRunWindowError as exc:
        # A required source (a window PL blob) was absent: refuse the partial
        # window loudly, recorded as a typed skip carrying the kernel's rule id.
        return WindowResult(
            window=window,
            status="skipped",
            skip_rule_id=exc.rule_id,
            derived_window_laws=tuple(locators),
        )

    summary = report.summary()
    ns = summary["north_star"]
    disp = summary["residual_disposition_counts"]
    return WindowResult(
        window=window,
        status="evaluated",
        derived_window_laws=tuple(locators),
        report=report,
        oracle_changed=ns["oracle_changed_section_count"],
        agreements=ns["sections_materialized_in_agreement"],
        lawvm_wrong=disp.get("lawvm_wrong", 0),
        oracle_suspect=disp.get("oracle_suspect", 0),
        missing_source=ns["missing_source_section_count"],
        sunset_reversion=ns["sunset_reversion_section_count"],
        refusals=summary["sections_refused"],
        coverage_fraction=ns["coverage_fraction"],
    )


def run_bench(
    archive: UsArchiveReader,
    windows: Iterable[BenchWindow],
    *,
    corpus_path: str = "",
) -> BenchReport:
    """Evaluate every ``include=true`` window; record ``include=false`` as skips."""
    report = BenchReport(corpus_path=corpus_path)
    for window in windows:
        if not window.include:
            report.results.append(
                WindowResult(
                    window=window,
                    status="skipped",
                    skip_rule_id=US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID,
                )
            )
            continue
        report.results.append(evaluate_window(archive, window))
    return report


# ---------------------------------------------------------------------------
# CLI rendering
# ---------------------------------------------------------------------------


def _render_table(report: BenchReport) -> str:
    rows = [
        "| window | oracle Δ | agree | cov | lawvm_wrong | oracle_suspect "
        "| missing_src | sunset | refused |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.evaluated():
        cov = "-" if r.coverage_fraction is None else f"{r.coverage_fraction:.3f}"
        rows.append(
            f"| {r.window.key} | {r.oracle_changed} | {r.agreements} | {cov} "
            f"| {r.lawvm_wrong} | {r.oracle_suspect} | {r.missing_source} "
            f"| {r.sunset_reversion} | {r.refusals} |"
        )
    skipped = report.skipped()
    if skipped:
        rows.append("")
        rows.append("Skipped windows:")
        for r in skipped:
            rows.append(f"  - {r.window.key}: {r.skip_rule_id}")
    return "\n".join(rows)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the U.S. federal dry-run bench corpus.",
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS_PATH,
        help=f"Bench corpus CSV (default: {DEFAULT_CORPUS_PATH}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable JSON report instead of the table.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    corpus_path: Path = args.corpus
    if not corpus_path.exists():
        print(f"error: bench corpus not found: {corpus_path}", file=sys.stderr)
        return 1
    windows = load_corpus(corpus_path)

    archive = open_us_federal_farchive(readonly=True)
    try:
        report = run_bench(archive, windows, corpus_path=str(corpus_path))
    finally:
        archive.close()

    if args.json:
        print(json.dumps(report.to_jsonable(), indent=2, sort_keys=True))
        return 0

    print(_render_table(report))
    agg = report.aggregate()
    cov = agg["coverage_fraction"]
    cov_str = "-" if cov is None else f"{cov:.4f}"
    print()
    print(
        f"AGGREGATE  windows={agg['windows_evaluated']} "
        f"(skipped {agg['windows_skipped']})  "
        f"witness-anchored coverage={agg['agreements_total']}/"
        f"{agg['oracle_changed_section_total']} = {cov_str}"
    )
    print(f"  disposition breakdown: {agg['disposition_breakdown']}")
    print(f"  refusals total: {agg['refusals_total']}")
    print("  replay_authorized: False (dry-run gate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

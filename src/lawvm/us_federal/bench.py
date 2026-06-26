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
import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence, assert_never

if TYPE_CHECKING:
    from lawvm.core.bench_contract import BenchUnitResult

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

# The titles of the U.S. Code that Congress has enacted into POSITIVE LAW (the
# title itself is the law; amendments hit the USC tree directly). The remaining
# titles are NON-POSITIVE editorial compilations (the underlying Statutes at Large
# are the law; OLRC classifies amendments into them, producing a large editorial
# reclassification/renumbering tail with no one-to-one amendment behind each delta).
# Partitioning the bench by this class is the honest way to show replay accuracy
# where the amendment->USC structure is clean, without conflating populations.
# Canonical enumeration per the OLRC (uscode.house.gov, positive-law table); stable.
POSITIVE_LAW_TITLES = frozenset(
    {1, 3, 4, 5, 9, 10, 11, 13, 14, 17, 18, 23, 28, 31, 32, 35, 36, 37, 38, 39,
     40, 41, 44, 46, 49, 51, 54}
)


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


class WindowStatus(StrEnum):
    """Closed set of per-window bench outcomes.

    A ``StrEnum`` so existing string consumers (JSON dict keys, test
    ``== "..."`` comparisons) keep working byte-for-byte while the value set is
    closed and dispatch can be made exhaustive.
    """

    EVALUATED = "evaluated"
    """The window was scored against the oracle."""

    SKIPPED = "skipped"
    """The window was skipped (see ``skip_rule_id``)."""


@dataclass
class WindowResult:
    """The per-window evaluation outcome (or a typed skip)."""

    window: BenchWindow
    status: WindowStatus
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
    deferred_op: int = 0
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
        match self.status:
            case WindowStatus.SKIPPED:
                payload["skip_rule_id"] = self.skip_rule_id
                payload["derived_window_law_count"] = len(self.derived_window_laws)
                return payload
            case WindowStatus.EVALUATED:
                pass
            case _ as unreachable:
                assert_never(unreachable)
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
                "deferred_op": self.deferred_op,
                "refusals": self.refusals,
            }
        )
        return payload


# ---------------------------------------------------------------------------
# Unified cross-jurisdiction bench contract — US federal adapter
#
# Re-house the existing US verified-agreement coverage into a contract
# BenchUnitResult without changing it. US scores per window:
# - structural_err = 1 - agreements / oracle_changed (verified-agreement coverage)
# - text_err       = None (count-based, no continuous text axis)
# residue is the non-agreement sections (lawvm_wrong / oracle_suspect /
# missing_source / sunset_reversion and any remainder), which is non-zero iff
# coverage is below 1 — so the structural error reconciles.
# See lawvm.core.bench_comparator_registry and notes/UNIFIED_BENCH_CONTRACT.md.
# ---------------------------------------------------------------------------


def us_bench_unit_result(result: "WindowResult") -> "BenchUnitResult":
    """Map a US ``WindowResult`` onto a contract ``BenchUnitResult``."""
    from lawvm.core.bench_contract import BenchStatus, BenchUnitResult

    unit_id = result.window.key
    if result.status is not WindowStatus.EVALUATED:
        # Typed skip — non-scored, not a failure (mirrors the US aggregate, which
        # only scores evaluated windows).
        return BenchUnitResult(unit_id=unit_id, status=BenchStatus.NO_TRUTH)
    if result.oracle_changed <= 0:
        # No oracle-changed sections in this window — nothing to score against.
        return BenchUnitResult(unit_id=unit_id, status=BenchStatus.NO_TRUTH)

    non_agreement = max(0, result.oracle_changed - result.agreements)
    structural_err = non_agreement / result.oracle_changed
    residue: dict[str, int] = {}
    if non_agreement > 0:
        for name, count in (
            ("lawvm_wrong", result.lawvm_wrong),
            ("oracle_suspect", result.oracle_suspect),
            ("missing_source", result.missing_source),
            ("sunset_reversion", result.sunset_reversion),
            ("deferred_op", result.deferred_op),
        ):
            if count:
                residue[name] = int(count)
        accounted = sum(residue.values())
        remainder = non_agreement - accounted
        if remainder > 0:
            # Non-agreement sections not captured by a named disposition bucket;
            # record them explicitly so the structural error is never silently
            # unexplained.
            residue["unclassified_non_agreement"] = remainder
        if not residue:
            residue["unclassified_non_agreement"] = non_agreement
    return BenchUnitResult(
        unit_id=unit_id,
        status=BenchStatus.SCORED,
        structural_err=structural_err,
        text_err=None,
        residue_buckets=residue,
    )


def _register_us_bench_comparator() -> None:
    from lawvm.core.bench_comparator_registry import register_bench_comparator

    register_bench_comparator("us", us_bench_unit_result)


_register_us_bench_comparator()


def _coverage_by_title_class(ev: list["WindowResult"]) -> dict[str, Any]:
    """Partition agreements/oracle-changed by positive-law vs non-positive title.

    Each class fraction divides only within its own title class, so neither number
    is diluted by the other class's denominator. Non-positive titles carry the
    OLRC editorial-reclassification tail; positive-law titles do not.
    """

    def _bucket(predicate) -> dict[str, Any]:
        rows = [r for r in ev if predicate(r.window.title)]
        denom = sum(r.oracle_changed for r in rows)
        numer = sum(r.agreements for r in rows)
        return {
            "numerator": numer,
            "denominator": denom,
            "fraction": (numer / denom) if denom else None,
            "titles": sorted({r.window.title for r in rows}),
        }

    return {
        "positive_law": _bucket(lambda t: t in POSITIVE_LAW_TITLES),
        "non_positive": _bucket(lambda t: t not in POSITIVE_LAW_TITLES),
    }


@dataclass
class BenchReport:
    """Aggregate across the whole bench corpus."""

    corpus_path: str
    results: list[WindowResult] = field(default_factory=list)

    def evaluated(self) -> list[WindowResult]:
        return [r for r in self.results if r.status is WindowStatus.EVALUATED]

    def skipped(self) -> list[WindowResult]:
        return [r for r in self.results if r.status is WindowStatus.SKIPPED]

    def aggregate(self) -> dict[str, Any]:
        ev = self.evaluated()
        denom = sum(r.oracle_changed for r in ev)
        numer = sum(r.agreements for r in ev)
        # Structurally-unwitnessable sections: oracle changes for which no Public Law
        # amendment source can ever account.
        #
        # - ``oracle_suspect``: the oracle itself has an editorial pathology (the OLRC
        #   consolidated text diverges from the enacted text for non-amendment reasons —
        #   the generalized F1 class). Our materialization is faithful to the enacted
        #   instruction; the oracle-side gap is editorial. No amendment source explains
        #   this change, so it is not a lowering-yield residual.
        # - ``sunset_reversion``: a temporary provision expired and the section reverted
        #   to its prior permanent form (F2, temporal layer). The change is driven by
        #   time, not by a Public Law amendatory instruction, so no amendment source can
        #   account for it.
        #
        # ``source_present_denom`` is the denominator restricted to oracle-changed
        # sections for which an amendment source plausibly exists.  Excludes only what
        # can be defended as structurally sourceless; ``missing_source`` (lowering gap)
        # stays IN the denominator — those are genuine amendments we haven't lowered yet.
        #
        # Honest limit: ``lawvm_wrong`` sections are NOT excluded because a wrong
        # materialization still has an underlying amendment source — we just got the
        # lowering wrong. Only the no-source-possible buckets are subtracted.
        n_oracle_suspect = sum(r.oracle_suspect for r in ev)
        n_sunset_reversion = sum(r.sunset_reversion for r in ev)
        n_deferred_op = sum(r.deferred_op for r in ev)
        # Structurally-unwitnessable: oracle_suspect (F1 editorial) + sunset_reversion
        # (F2 temporal reversion) + deferred_op (F3 OLRC editorial pre-dating of a
        # future-effective amendment — LawVM lowered the right op but the after-edition
        # cutoff precedes its effective date; the OLRC pre-incorporated the text).
        n_structurally_unwitnessable = n_oracle_suspect + n_sunset_reversion + n_deferred_op
        source_present_denom = denom - n_structurally_unwitnessable
        return {
            "windows_evaluated": len(ev),
            "windows_skipped": len(self.skipped()),
            "oracle_changed_section_total": denom,
            "agreements_total": numer,
            # coverage_all: agreements / ALL oracle-changed sections (the monotone
            # north-star denominator — a fact of the two USC editions).
            "coverage_fraction": (numer / denom) if denom else None,
            "coverage_all": {
                "numerator": numer,
                "denominator": denom,
                "fraction": (numer / denom) if denom else None,
                "denominator_note": (
                    "all oracle-changed sections regardless of source availability"
                ),
            },
            # coverage_source_present: agreements / (oracle-changed − structurally-
            # unwitnessable).  Excluded: oracle_suspect (OLRC editorial pathology, F1)
            # + sunset_reversion (temporal reversion, F2) + deferred_op (F3, OLRC
            # editorial pre-dating of future-effective amendments).  Included:
            # missing_source (genuine lowering gap — amendment exists, we haven't
            # lowered it yet) and lawvm_wrong (amendment exists, our lowering is wrong).
            "coverage_source_present": {
                "numerator": numer,
                "denominator": source_present_denom,
                "fraction": (numer / source_present_denom) if source_present_denom else None,
                "excluded_oracle_suspect": n_oracle_suspect,
                "excluded_sunset_reversion": n_sunset_reversion,
                "excluded_deferred_op": n_deferred_op,
                "excluded_total": n_structurally_unwitnessable,
                "denominator_note": (
                    "oracle-changed sections minus structurally-unwitnessable buckets "
                    "(oracle_suspect = OLRC editorial pathology F1; "
                    "sunset_reversion = temporal reversion F2; "
                    "deferred_op = OLRC pre-dating F3); "
                    "missing_source and lawvm_wrong remain in denominator"
                ),
            },
            # coverage_by_title_class: the same numerator/denominator partitioned by
            # whether the title is positive law (amendments hit the USC tree directly,
            # clean structure) or non-positive (editorial classification tail dominates
            # the denominator). This splits the denominator's OWN population — no
            # cross-population conflation — so each fraction is an honest coverage of
            # that class's oracle-changed sections.
            "coverage_by_title_class": _coverage_by_title_class(ev),
            "disposition_breakdown": {
                "agreement": numer,
                "lawvm_wrong": sum(r.lawvm_wrong for r in ev),
                "oracle_suspect": n_oracle_suspect,
                "missing_source": sum(r.missing_source for r in ev),
                "sunset_reversion": n_sunset_reversion,
                "deferred_op": sum(r.deferred_op for r in ev),
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
            status=WindowStatus.SKIPPED,
            skip_rule_id=US_BENCH_WINDOW_EDITION_MISSING_RULE_ID,
        )
    if not locators:
        return WindowResult(
            window=window,
            status=WindowStatus.SKIPPED,
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
            status=WindowStatus.SKIPPED,
            skip_rule_id=exc.rule_id,
            derived_window_laws=tuple(locators),
        )

    summary = report.summary()
    ns = summary["north_star"]
    disp = summary["residual_disposition_counts"]
    return WindowResult(
        window=window,
        status=WindowStatus.EVALUATED,
        derived_window_laws=tuple(locators),
        report=report,
        oracle_changed=ns["oracle_changed_section_count"],
        agreements=ns["sections_materialized_in_agreement"],
        lawvm_wrong=disp.get("lawvm_wrong", 0),
        oracle_suspect=disp.get("oracle_suspect", 0),
        missing_source=ns["missing_source_section_count"],
        sunset_reversion=ns["sunset_reversion_section_count"],
        deferred_op=ns.get("deferred_op_section_count", 0),
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
                    status=WindowStatus.SKIPPED,
                    skip_rule_id=US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID,
                )
            )
            continue
        report.results.append(evaluate_window(archive, window))
    return report


# ---------------------------------------------------------------------------
# Parallel bench (determinism contract: byte-identical aggregate to run_bench)
# ---------------------------------------------------------------------------
#
# The bench over a 54-title × 1994-2024 ceiling is hundreds-to-thousands of
# windows; ``run_bench`` evaluates them serially. ``run_bench_parallel`` shards
# the windows across worker processes and reassembles in *corpus order* so the
# aggregate is byte-identical to the serial runner regardless of worker count or
# completion scheduling.
#
# DETERMINISM CONTRACT (non-negotiable)
# -------------------------------------
#   * ``evaluate_window(archive, window)`` is pure given the archive + window, so
#     each window's :class:`WindowResult` is independent of every other.
#   * The final report's ``results`` list is in the original corpus order (each
#     window is tagged with its corpus index; results are reassembled ascending),
#     never completion order. Skips for ``include=false`` windows are produced in
#     the same place the serial runner produces them.
#   * Each worker opens its OWN read-only farchive handle via the picklable
#     :func:`_build_us_bench_store` factory — a SQLite connection is never shared
#     across processes. The farchive opens ``readonly=True`` so parallel readers
#     are safe.
#
# Worker results carry ``report=None``: the heavy :class:`USDryRunReport` object
# is dropped at the process boundary (the aggregate, the table and
# ``to_jsonable`` all read only the projected scalars, so the JSON and aggregate
# stay byte-identical). This also bounds per-worker resident memory.

# Hard pool-size ceiling. Each worker holds an open farchive handle and replays a
# window at a time, so this bounds resident memory under the WSL2 ceiling
# regardless of host core count or an over-large explicit ``--parallel``. Matches
# the ``min(16, cpu-2)`` style cap used by the other parallel corpus paths.
US_BENCH_MAX_WORKERS = 16


def _build_us_bench_store() -> UsArchiveReader:
    """Per-worker store factory: one read-only farchive handle per process.

    Module-level (hence picklable) so the process pool can resolve it by
    ``(module, qualname)`` reference. Mirrors the UK self-consistency
    ``build_uk_store`` contract: one open handle per worker, used read-only.
    """
    return open_us_federal_farchive(readonly=True)


_WORKER_STORE: UsArchiveReader | None = None


def _worker_init() -> None:
    """Process-pool initializer: open this worker's own read-only farchive once."""
    global _WORKER_STORE
    _WORKER_STORE = _build_us_bench_store()


def _worker_run_shard(
    task: tuple[int, Sequence[tuple[int, BenchWindow]]],
) -> tuple[int, list[tuple[int, WindowResult]]]:
    """Evaluate one shard of ``(corpus_index, window)`` items.

    Returns ``(shard_index, [(corpus_index, WindowResult), ...])`` with the heavy
    report object stripped from each result. ``include=false`` windows are turned
    into the same typed skip the serial runner emits, so the parallel and serial
    ``results`` lists are element-for-element identical.
    """
    shard_index, items = task
    assert _WORKER_STORE is not None, "worker store not initialized"
    out: list[tuple[int, WindowResult]] = []
    for corpus_index, window in items:
        if not window.include:
            result = WindowResult(
                window=window,
                status=WindowStatus.SKIPPED,
                skip_rule_id=US_BENCH_WINDOW_EMPTY_DELTA_RULE_ID,
            )
        else:
            result = evaluate_window(_WORKER_STORE, window)
            if result.report is not None:
                result = dataclasses.replace(result, report=None)
        out.append((corpus_index, result))
    return shard_index, out


def _make_window_shards(
    indexed: Sequence[tuple[int, BenchWindow]], workers: int
) -> list[tuple[int, list[tuple[int, BenchWindow]]]]:
    """Split indexed windows into contiguous, order-preserving shards.

    Contiguous (not round-robin) shards keep each shard's windows in corpus order
    and make the ascending-shard concatenation reproduce the serial order
    exactly. Aim for several shards per worker so uneven per-window replay cost
    still balances, while keeping shards large enough to amortize dispatch.
    """
    n = len(indexed)
    if n == 0:
        return []
    target_shards = max(workers, min(n, workers * 4))
    chunk = (n + target_shards - 1) // target_shards
    shards: list[tuple[int, list[tuple[int, BenchWindow]]]] = []
    idx = 0
    start = 0
    while start < n:
        shards.append((idx, list(indexed[start:start + chunk])))
        idx += 1
        start += chunk
    return shards


def run_bench_parallel(
    windows: Iterable[BenchWindow],
    *,
    workers: int,
    corpus_path: str = "",
) -> BenchReport:
    """Parallel sibling of :func:`run_bench`, byte-identical in the aggregate.

    Shards the corpus windows across ``workers`` processes (each with its own
    read-only farchive handle) and reassembles the per-window results in corpus
    order. ``workers <= 1`` (or a corpus of <= 1 window) falls back to the serial
    :func:`run_bench` over a single shared handle, so the parallel path is always
    a safe superset of the serial one.

    Unlike :func:`run_bench`, this opens the archive itself (one handle per
    worker) rather than taking a shared handle — a SQLite connection must never
    cross a process boundary.
    """
    window_list = list(windows)
    if workers <= 0:
        workers = max(1, (os.cpu_count() or 2) - 2)
    workers = min(workers, US_BENCH_MAX_WORKERS)

    # Serial fast path / fallback: identical to run_bench over one handle.
    if workers <= 1 or len(window_list) <= 1:
        archive = open_us_federal_farchive(readonly=True)
        try:
            return run_bench(archive, window_list, corpus_path=corpus_path)
        finally:
            archive.close()

    from concurrent.futures import as_completed

    from lawvm.tools._worker_pool import managed_executor

    indexed = list(enumerate(window_list))
    shards = _make_window_shards(indexed, workers)

    # corpus_index -> WindowResult, for stable reassembly in corpus order.
    collected: dict[int, WindowResult] = {}
    with managed_executor(workers, initializer=_worker_init, initargs=()) as pool:
        futures = {pool.submit(_worker_run_shard, task): task[0] for task in shards}
        for future in as_completed(futures):
            _shard_index, pairs = future.result()
            for corpus_index, result in pairs:
                collected[corpus_index] = result

    report = BenchReport(corpus_path=corpus_path)
    report.results = [collected[i] for i in range(len(window_list))]
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
    parser.add_argument(
        "--parallel",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Shard windows across N worker processes (each opens its own "
            f"read-only farchive handle, capped at {US_BENCH_MAX_WORKERS}). The "
            "aggregate is byte-identical to the serial runner. 0/1 (default) "
            "runs serially."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    corpus_path: Path = args.corpus
    if not corpus_path.exists():
        print(f"error: bench corpus not found: {corpus_path}", file=sys.stderr)
        return 1
    windows = load_corpus(corpus_path)

    if args.parallel and args.parallel > 1:
        # Parallel path opens one read-only handle per worker itself.
        report = run_bench_parallel(
            windows, workers=args.parallel, corpus_path=str(corpus_path)
        )
    else:
        archive = open_us_federal_farchive(readonly=True)
        try:
            report = run_bench(archive, windows, corpus_path=str(corpus_path))
        finally:
            archive.close()

    if args.json:
        print(json.dumps(report.to_jsonable(), indent=2, sort_keys=True))
        return 0

    print(_render_table(report))

    # Unified cross-jurisdiction headline (worst-of axes over the contract
    # BenchUnitResults). US has no text axis, so the headline is the structural
    # (verified-agreement coverage) axis; render_summary omits the text line.
    from lawvm.core.bench_aggregate import render_summary

    unit_results = [us_bench_unit_result(r) for r in report.results]
    print()
    for line in render_summary(unit_results, corpus_path.stem, jurisdiction="us"):
        print(line)

    agg = report.aggregate()
    print()
    print(
        f"AGGREGATE  windows={agg['windows_evaluated']} "
        f"(skipped {agg['windows_skipped']})"
    )
    # coverage_all: the monotone north-star, full oracle-changed denominator.
    ca = agg["coverage_all"]
    ca_str = "-" if ca["fraction"] is None else f"{ca['fraction']:.4f}"
    print(
        f"  coverage_all (agreements / all oracle-changed):  "
        f"{ca['numerator']}/{ca['denominator']} = {ca_str}"
    )
    # coverage_source_present: same numerator, denominator minus structurally-
    # unwitnessable buckets (oracle_suspect F1 + sunset_reversion F2).
    sp = agg["coverage_source_present"]
    sp_str = "-" if sp["fraction"] is None else f"{sp['fraction']:.4f}"
    excluded = sp["excluded_total"]
    print(
        f"  coverage_source_present (excl. {excluded} structurally-unwitnessable):  "
        f"{sp['numerator']}/{sp['denominator']} = {sp_str}"
    )
    print(
        f"    excluded: oracle_suspect(F1)={sp['excluded_oracle_suspect']}  "
        f"sunset_reversion(F2)={sp['excluded_sunset_reversion']}"
    )
    # coverage_by_title_class: positive-law (clean amendment->USC structure) vs
    # non-positive (editorial-reclassification tail), each within its own population.
    tc = agg["coverage_by_title_class"]
    for label, key in (("positive_law", "positive_law"), ("non_positive", "non_positive")):
        b = tc[key]
        b_str = "-" if b["fraction"] is None else f"{b['fraction']:.4f}"
        print(
            f"  coverage[{label}] ({len(b['titles'])} titles):  "
            f"{b['numerator']}/{b['denominator']} = {b_str}"
        )
    print(f"  disposition breakdown: {agg['disposition_breakdown']}")
    print(f"  refusals total: {agg['refusals_total']}")
    print("  replay_authorized: False (dry-run gate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Corpus-wide aggregator for the NZ all-families amendment-chain replay.

The per-work chain replay (:mod:`lawvm.new_zealand.chain_replay`) carries a
single evolving tree across a base work's whole amendment chain and reports a
similarity CURVE vs the archived oracle at every version date. That surface is
proven per-work; this module runs it across a whole work POPULATION and
aggregates the honest corpus-wide end-to-end numbers:

- the per-work FINAL stable-combined similarity DISTRIBUTION (count, mean,
  median, p25/p75, a small histogram) — the honest corpus e2e number, the
  chain-replay analogue of the dry-run north-star;
- per-family applied / skipped / oracle-agreement totals across the corpus;
- the ranked SKIP/EXTRACTION cap census (which extraction gap dominates
  corpus-wide), so the next extraction lane can be ordered data-first;
- the shared-mean (surviving-node text) distribution as a secondary signal.

It inherits the boring discipline of the single-work surface: it never enables
actual replay (``replay_claims`` stays ``False``), never mutates the archive, and
never turns the oracle into source truth. It reports the raw distribution and
does not flatter — every non-applied op is a typed, visible skip residual.

PARALLELISM + DETERMINISM
-------------------------
Per-work chain replays are independent and CPU-bound (lxml parse + per-op
mutation kernels), so they run in a :class:`ProcessPoolExecutor`. Each worker
process activates its own run-scoped parse/archive cache
(:func:`lawvm.new_zealand.corpus_cache.corpus_run_cache`), opening the farchive
read-only once per worker and parsing each archived version XML at most once for
the works it handles. The pool is fed in the deterministic selection order and
the per-work results are re-sorted by work_id before aggregation, so the
aggregate output is byte-identical regardless of worker count or completion
order. No clock, no randomness enters any reported value.

NO SILENT CAPS
--------------
The slice actually run is always stated alongside the available total and
whether ``--max-works`` bit. A per-work chain replay that raises is recorded as
a typed ``error`` result (never dropped); errored works are counted and excluded
from the similarity distribution with the exclusion stated.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.benchmark import NZBenchmarkSelectionError, select_benchmark_work_ids
from lawvm.new_zealand.chain_replay import (
    CHAIN_FAMILY_ORDER,
    NZ_CHAIN_REPLAY_REPLAY_CLAIMS,
    NZ_CHAIN_REPLAY_TRUTH_CLAIM,
    build_archived_work_chain_replay,
    resolve_families,
)
from lawvm.new_zealand.corpus_cache import active_corpus_run_cache, corpus_run_cache

# Default worker count for the per-work process pool. CPU-bound; capped so a
# corpus run does not starve a shared box. Overridable via --workers.
DEFAULT_WORKERS = 8

# Number of selected work ids echoed back in the selection-context sample.
_SELECTION_WORK_ID_SAMPLE_LIMIT = 50

# Number of exemplar work ids retained per skip-cap bucket.
_CAP_EXEMPLAR_LIMIT = 5

# Histogram bin edges for the [0, 1] similarity distribution. Ten equal bins;
# the final bin is closed on the right so a perfect 1.0 lands in the top bin.
_HISTOGRAM_BIN_COUNT = 10


@dataclass(frozen=True)
class NZChainReplayWorkResult:
    """Compact per-work chain-replay result for corpus aggregation.

    Carries only the aggregate-relevant projections of the full per-work
    :class:`~lawvm.new_zealand.chain_replay.NZChainReplayReport` (the headline
    final stable-combined similarity, the secondary shared-mean, per-family
    counts, and the typed skip-bucket census), so the result is cheap to ship
    back across the process-pool boundary. ``error`` is a non-empty diagnostic
    string when the per-work replay raised — such a work is counted, never
    silently dropped, and is excluded from the similarity distribution.
    """

    work_id: str
    families_requested: tuple[str, ...]
    n_archived_versions: int
    n_transitions: int
    total_ops: int
    ops_applied: int
    ops_skipped: int
    # Final-version metrics (None when there is no similarity curve at all, e.g.
    # a work with no archived versions; such a work is excluded from the
    # distribution with that exclusion stated).
    final_combined_similarity_stable: float | None
    final_combined_similarity_raw: float | None
    final_shared_mean_similarity: float | None
    # Per-family (enumerated, applied, skipped, oracle_agree, oracle_total).
    per_family: dict[str, tuple[int, int, int, int, int]]
    # Skip-cap census: bucket -> count for this work.
    skip_bucket_counts: dict[str, int]
    n_divergences: int
    error: str = ""

    @property
    def is_error(self) -> bool:
        return bool(self.error)

    @property
    def has_final(self) -> bool:
        return self.final_combined_similarity_stable is not None

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "families_requested": list(self.families_requested),
            "n_archived_versions": self.n_archived_versions,
            "n_transitions": self.n_transitions,
            "total_ops": self.total_ops,
            "ops_applied": self.ops_applied,
            "ops_skipped": self.ops_skipped,
            "final_combined_similarity_stable": _round_opt(self.final_combined_similarity_stable),
            "final_combined_similarity_raw": _round_opt(self.final_combined_similarity_raw),
            "final_shared_mean_similarity": _round_opt(self.final_shared_mean_similarity),
            "per_family": {
                family: {
                    "enumerated": stat[0],
                    "applied": stat[1],
                    "skipped": stat[2],
                    "oracle_agreements": stat[3],
                    "oracle_total": stat[4],
                }
                for family, stat in sorted(self.per_family.items())
            },
            "skip_bucket_counts": dict(sorted(self.skip_bucket_counts.items())),
            "n_divergences": self.n_divergences,
            "error": self.error,
        }


def _project_work_result(work_id: str, report: Any) -> NZChainReplayWorkResult:
    """Project a full per-work report down to the compact corpus result."""

    final = report.final_similarity()
    per_family: dict[str, tuple[int, int, int, int, int]] = {}
    for stat in report.per_family_stats:
        per_family[stat.family] = (
            stat.enumerated,
            stat.applied,
            stat.skipped,
            stat.oracle_agreements,
            stat.oracle_agreements + stat.oracle_disagreements,
        )
    total_ops = sum(t.n_ops for t in report.transitions)
    return NZChainReplayWorkResult(
        work_id=work_id,
        families_requested=tuple(report.families_requested),
        n_archived_versions=report.n_archived_versions,
        n_transitions=len(report.transitions),
        total_ops=total_ops,
        ops_applied=report.repeals_applied,
        ops_skipped=report.repeals_skipped,
        final_combined_similarity_stable=(
            final.combined_similarity_stable if final is not None else None
        ),
        final_combined_similarity_raw=(final.combined_similarity if final is not None else None),
        final_shared_mean_similarity=(final.shared_mean_similarity if final is not None else None),
        per_family=per_family,
        skip_bucket_counts=report.skip_bucket_counts(),
        n_divergences=len(report.divergences),
    )


def _run_one_work(db_path_str: str, work_id: str, families_spec: str) -> NZChainReplayWorkResult:
    """Worker entry point: run the per-work chain replay for one work.

    Runs inside a worker process under that worker's run-scoped cache (activated
    by :func:`_worker_run_one`). A per-work failure is captured as a typed error
    result rather than propagated, so one bad work does not abort the corpus run
    (the error is reported, never hidden).
    """

    try:
        report = build_archived_work_chain_replay(
            Path(db_path_str), work_id, families=families_spec
        )
    except Exception as exc:  # noqa: BLE001 - per-work isolation; the error is reported
        return NZChainReplayWorkResult(
            work_id=work_id,
            families_requested=tuple(resolve_families(families_spec) & set(CHAIN_FAMILY_ORDER)),
            n_archived_versions=0,
            n_transitions=0,
            total_ops=0,
            ops_applied=0,
            ops_skipped=0,
            final_combined_similarity_stable=None,
            final_combined_similarity_raw=None,
            final_shared_mean_similarity=None,
            per_family={},
            skip_bucket_counts={},
            n_divergences=0,
            error=f"{type(exc).__name__}: {exc}",
        )
    return _project_work_result(work_id, report)


# Module-level worker state: each worker process opens its own run-scoped cache
# once (via the pool initializer) and reuses it for every work it handles, so the
# farchive is opened once per worker and each archived version parses at most once
# per worker. The cache context manager is held open for the worker's lifetime.
_WORKER_CACHE_CM: Any = None


def _worker_init() -> None:
    global _WORKER_CACHE_CM
    _WORKER_CACHE_CM = corpus_run_cache()
    _WORKER_CACHE_CM.__enter__()


def _worker_run_one(args: tuple[str, str, str]) -> NZChainReplayWorkResult:
    db_path_str, work_id, families_spec = args
    result = _run_one_work(db_path_str, work_id, families_spec)
    # Bound per-worker memory: distinct works share ~no archived XML, so drop the
    # parsed-document memo between works while keeping the shared archive handle.
    cache = active_corpus_run_cache()
    if cache is not None:
        cache.reset_parsed()
    return result


# --- Pure aggregation helpers (unit-testable without the archive) ---


@dataclass(frozen=True)
class NZSimilarityDistribution:
    """Distribution summary of a per-work similarity sample over ``[0, 1]``."""

    count: int
    mean: float | None
    median: float | None
    p25: float | None
    p75: float | None
    minimum: float | None
    maximum: float | None
    histogram: tuple[int, ...]  # _HISTOGRAM_BIN_COUNT bins over [0, 1]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean": _round_opt(self.mean),
            "median": _round_opt(self.median),
            "p25": _round_opt(self.p25),
            "p75": _round_opt(self.p75),
            "min": _round_opt(self.minimum),
            "max": _round_opt(self.maximum),
            "histogram_bins": list(self.histogram),
            "histogram_bin_edges": [round(i / _HISTOGRAM_BIN_COUNT, 2) for i in range(_HISTOGRAM_BIN_COUNT + 1)],
        }


def _percentile_sorted(sorted_values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile of an already-sorted, non-empty list.

    Deterministic (the classic ``rank = fraction * (n - 1)`` interpolation); no
    numpy dependency so the arithmetic is auditable and identical everywhere.
    """

    if not sorted_values:
        raise ValueError("percentile of empty sample")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = fraction * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    weight = rank - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _histogram(values: list[float], *, bins: int = _HISTOGRAM_BIN_COUNT) -> tuple[int, ...]:
    """Bin ``[0, 1]`` values into ``bins`` equal buckets (top bin closed right)."""

    counts = [0] * bins
    for value in values:
        clamped = min(max(value, 0.0), 1.0)
        index = int(clamped * bins)
        if index >= bins:  # value == 1.0 lands in the final bin
            index = bins - 1
        counts[index] += 1
    return tuple(counts)


def summarize_distribution(values: list[float]) -> NZSimilarityDistribution:
    """Summarize a per-work similarity sample (count/mean/median/quartiles/hist).

    Empty sample -> a zero-count distribution with ``None`` statistics (an honest
    "no works contributed" rather than a misleading 0.0 or 1.0).
    """

    if not values:
        return NZSimilarityDistribution(
            count=0,
            mean=None,
            median=None,
            p25=None,
            p75=None,
            minimum=None,
            maximum=None,
            histogram=tuple([0] * _HISTOGRAM_BIN_COUNT),
        )
    ordered = sorted(values)
    return NZSimilarityDistribution(
        count=len(ordered),
        mean=sum(ordered) / len(ordered),
        median=_percentile_sorted(ordered, 0.5),
        p25=_percentile_sorted(ordered, 0.25),
        p75=_percentile_sorted(ordered, 0.75),
        minimum=ordered[0],
        maximum=ordered[-1],
        histogram=_histogram(ordered),
    )


def tally_skip_caps(
    results: tuple[NZChainReplayWorkResult, ...],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Tally the corpus-wide skip/extraction-cap census + per-cap exemplars.

    Returns ``(counts, exemplars)`` where ``counts`` maps each skip bucket to its
    total occurrence count across the corpus and ``exemplars`` maps each bucket
    to up to :data:`_CAP_EXEMPLAR_LIMIT` exemplar work ids (in deterministic
    work-id order). This is the ranked next-lever: the dominant bucket is the
    highest-EV extraction gap to close.
    """

    counts: dict[str, int] = {}
    exemplars: dict[str, list[str]] = {}
    for result in sorted(results, key=lambda r: r.work_id):
        for bucket, count in result.skip_bucket_counts.items():
            counts[bucket] = counts.get(bucket, 0) + count
            bucket_exemplars = exemplars.setdefault(bucket, [])
            if len(bucket_exemplars) < _CAP_EXEMPLAR_LIMIT and result.work_id not in bucket_exemplars:
                bucket_exemplars.append(result.work_id)
    return counts, exemplars


def aggregate_per_family(
    results: tuple[NZChainReplayWorkResult, ...],
) -> dict[str, dict[str, int]]:
    """Sum per-family enumerated/applied/skipped/oracle counts across the corpus."""

    totals: dict[str, dict[str, int]] = {}
    for result in results:
        for family, stat in result.per_family.items():
            enumerated, applied, skipped, oracle_agree, oracle_total = stat
            bucket = totals.setdefault(
                family,
                {"enumerated": 0, "applied": 0, "skipped": 0, "oracle_agreements": 0, "oracle_total": 0},
            )
            bucket["enumerated"] += enumerated
            bucket["applied"] += applied
            bucket["skipped"] += skipped
            bucket["oracle_agreements"] += oracle_agree
            bucket["oracle_total"] += oracle_total
    return {family: totals[family] for family in CHAIN_FAMILY_ORDER if family in totals}


@dataclass(frozen=True)
class NZChainReplayCorpusReport:
    """Aggregate all-families chain-replay report across an NZ work population."""

    db_path: str
    families_requested: tuple[str, ...]
    results: tuple[NZChainReplayWorkResult, ...]
    requested_work_ids: tuple[str, ...] = ()
    selected_work_ids: tuple[str, ...] = ()
    available_work_count: int = 0
    max_works: int | None = None
    workers: int = DEFAULT_WORKERS
    wall_clock_seconds: float = 0.0

    def errored_results(self) -> tuple[NZChainReplayWorkResult, ...]:
        return tuple(r for r in self.results if r.is_error)

    def scored_results(self) -> tuple[NZChainReplayWorkResult, ...]:
        return tuple(r for r in self.results if r.has_final and not r.is_error)

    def selection_context(self) -> dict[str, Any]:
        selected = self.selected_work_ids or tuple(r.work_id for r in self.results)
        requested = self.requested_work_ids
        base_count = len(requested) if requested else self.available_work_count
        selected_sample = selected[:_SELECTION_WORK_ID_SAMPLE_LIMIT]
        return {
            "available_work_count": self.available_work_count,
            "requested_work_count": len(requested),
            "selected_work_count": len(selected),
            "selected_work_ids_sample": list(selected_sample),
            "selected_work_ids_omitted": max(len(selected) - len(selected_sample), 0),
            "max_works": self.max_works,
            # No silent truncation: state the cap and whether it actually bit.
            "truncated_by_max_works": self.max_works is not None and len(selected) < base_count,
        }

    def similarity_distribution(self) -> NZSimilarityDistribution:
        return summarize_distribution(
            [
                r.final_combined_similarity_stable
                for r in self.scored_results()
                if r.final_combined_similarity_stable is not None
            ]
        )

    def raw_similarity_distribution(self) -> NZSimilarityDistribution:
        return summarize_distribution(
            [
                r.final_combined_similarity_raw
                for r in self.scored_results()
                if r.final_combined_similarity_raw is not None
            ]
        )

    def shared_mean_distribution(self) -> NZSimilarityDistribution:
        return summarize_distribution(
            [
                r.final_shared_mean_similarity
                for r in self.scored_results()
                if r.final_shared_mean_similarity is not None
            ]
        )

    def summary(self) -> dict[str, Any]:
        scored = self.scored_results()
        errored = self.errored_results()
        no_final = tuple(
            r for r in self.results if not r.has_final and not r.is_error
        )
        cap_counts, cap_exemplars = tally_skip_caps(self.results)
        per_family = aggregate_per_family(self.results)
        total_divergences = sum(r.n_divergences for r in self.results)
        return {
            "db_path": self.db_path,
            "truth_claim": NZ_CHAIN_REPLAY_TRUTH_CLAIM,
            "replay_claims": NZ_CHAIN_REPLAY_REPLAY_CLAIMS,
            "families_requested": list(self.families_requested),
            "selection_context": self.selection_context(),
            "workers": self.workers,
            "wall_clock_seconds": round(self.wall_clock_seconds, 3),
            "works_attempted": len(self.results),
            "works_scored": len(scored),
            "works_errored": len(errored),
            "works_no_final_version": len(no_final),
            "errored_work_ids": [r.work_id for r in sorted(errored, key=lambda x: x.work_id)][
                :_SELECTION_WORK_ID_SAMPLE_LIMIT
            ],
            # (a) The honest corpus e2e number: final stable-combined similarity.
            "final_stable_combined_similarity_distribution": (
                self.similarity_distribution().to_jsonable()
            ),
            "final_raw_combined_similarity_distribution": (
                self.raw_similarity_distribution().to_jsonable()
            ),
            # (d) Secondary signal: surviving-node shared-mean text similarity.
            "final_shared_mean_similarity_distribution": (
                self.shared_mean_distribution().to_jsonable()
            ),
            # (b) Per-family applied vs skipped vs oracle-agreement totals.
            "per_family_totals": per_family,
            # (c) The ranked skip/extraction-cap census (the next-lever).
            "skip_cap_census": _ranked_cap_list(cap_counts, cap_exemplars),
            "total_divergences": total_divergences,
        }

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "experimental_dry_run_chain_replay_corpus",
            "truth_claim": NZ_CHAIN_REPLAY_TRUTH_CLAIM,
            "replay_claims": NZ_CHAIN_REPLAY_REPLAY_CLAIMS,
            "summary": self.summary(),
        }
        if not summary_only:
            payload["works"] = [
                r.to_jsonable() for r in sorted(self.results, key=lambda x: x.work_id)
            ]
        return payload


def _ranked_cap_list(
    counts: dict[str, int], exemplars: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Order the cap census by descending count (ties broken by bucket name)."""

    return [
        {
            "bucket": bucket,
            "count": count,
            "exemplar_work_ids": exemplars.get(bucket, []),
        }
        for bucket, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _resolve_work_population(
    db_path: Path,
    *,
    work_ids: tuple[str, ...],
    max_works: int | None,
    work_id_prefix: str,
    min_version_year: int | None,
    sample_strategy: str,
) -> tuple[list[str], tuple[str, ...], int]:
    """Resolve ``(selected, requested, available)`` work ids deterministically.

    An explicit ``work_ids`` list wins (only ``max_works`` truncates it);
    otherwise the representative benchmark sampler selects the population. Opens
    the archive only to enumerate the available ids + run the sampler.
    """

    from lawvm.new_zealand.benchmark import _archived_work_max_version_year

    archive = open_farchive(db_path)
    try:
        archived_work_ids = tuple(_archived_work_max_version_year(archive))
        available = len(archived_work_ids)
        requested = tuple(dict.fromkeys(work_ids))
        if requested:
            selected = list(requested)
            if max_works is not None:
                selected = selected[: max(max_works, 0)]
        else:
            selected = list(
                select_benchmark_work_ids(
                    archive,
                    archived_work_ids=archived_work_ids,
                    work_id_prefix=work_id_prefix,
                    min_version_year=min_version_year,
                    sample_strategy=sample_strategy,
                    max_works=max_works,
                )
            )
    finally:
        archive.close()
    return selected, requested, available


def build_nz_chain_replay_corpus_report(
    db_path: Path,
    *,
    work_ids: tuple[str, ...] = (),
    max_works: int | None = None,
    work_id_prefix: str = "",
    min_version_year: int | None = None,
    sample_strategy: str = "head",
    families: str | frozenset[str] | None = None,
    workers: int = DEFAULT_WORKERS,
) -> NZChainReplayCorpusReport:
    """Run the all-families chain replay across a selected NZ work population.

    Per-work replays run in a process pool (``workers`` processes; each opens the
    farchive read-only once and parses each archived XML at most once for its
    works). Results are re-sorted by work_id before aggregation so the output is
    deterministic regardless of completion order. ``workers <= 1`` runs serially
    (used by the tests for determinism + to avoid a pool in-process).
    """

    resolved_families = resolve_families(families)
    families_requested = tuple(f for f in CHAIN_FAMILY_ORDER if f in resolved_families)
    families_spec = ",".join(families_requested) if families_requested else "all"

    selected, requested, available = _resolve_work_population(
        db_path,
        work_ids=work_ids,
        max_works=max_works,
        work_id_prefix=work_id_prefix,
        min_version_year=min_version_year,
        sample_strategy=sample_strategy,
    )

    db_path_str = str(db_path)
    start = time.monotonic()
    if workers <= 1:
        # Serial path: one run-scoped cache for the whole run (parses reused
        # within a work, dropped between works). Deterministic and pool-free.
        results_list: list[NZChainReplayWorkResult] = []
        with corpus_run_cache() as cache:
            for work_id in selected:
                results_list.append(_run_one_work(db_path_str, work_id, families_spec))
                cache.reset_parsed()
    else:
        tasks = [(db_path_str, work_id, families_spec) for work_id in selected]
        with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as pool:
            # ``map`` preserves input order, but we re-sort below regardless so the
            # aggregate never depends on scheduling.
            results_list = list(pool.map(_worker_run_one, tasks))
    wall_clock = time.monotonic() - start

    # Deterministic aggregation: sort by work_id (no clock/scheduling dependence).
    results = tuple(sorted(results_list, key=lambda r: r.work_id))

    return NZChainReplayCorpusReport(
        db_path=db_path_str,
        families_requested=families_requested,
        results=results,
        requested_work_ids=requested,
        selected_work_ids=tuple(selected),
        available_work_count=available,
        max_works=max_works,
        workers=workers,
        wall_clock_seconds=wall_clock,
    )


def _round_opt(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _fmt_opt(value: float | None) -> str:
    return f"{value:.4f}" if value is not None else "n/a"


def _print_distribution(label: str, dist: NZSimilarityDistribution) -> None:
    print(
        f"{label}: n={dist.count} "
        f"mean={_fmt_opt(dist.mean)} median={_fmt_opt(dist.median)} "
        f"p25={_fmt_opt(dist.p25)} p75={_fmt_opt(dist.p75)} "
        f"min={_fmt_opt(dist.minimum)} max={_fmt_opt(dist.maximum)}"
    )
    edges = [i / _HISTOGRAM_BIN_COUNT for i in range(_HISTOGRAM_BIN_COUNT + 1)]
    for index, count in enumerate(dist.histogram):
        bar = "#" * min(count, 60)
        print(f"    [{edges[index]:.1f},{edges[index + 1]:.1f})  {count:>5}  {bar}")


def main(args: Any) -> None:
    work_ids = tuple(getattr(args, "work_id", None) or ())
    corpus_path = getattr(args, "corpus", None)
    if corpus_path:
        from lawvm.new_zealand.bench_corpus import NZBenchCorpusError, read_corpus_work_ids

        try:
            corpus_work_ids = read_corpus_work_ids(Path(corpus_path))
        except NZBenchCorpusError as exc:
            raise SystemExit(f"nz-corpus replay-chain-corpus: {exc}") from exc
        if not work_ids:
            work_ids = corpus_work_ids

    try:
        report = build_nz_chain_replay_corpus_report(
            Path(args.db),
            work_ids=work_ids,
            max_works=getattr(args, "max_works", None),
            work_id_prefix=getattr(args, "work_id_prefix", "") or "",
            min_version_year=getattr(args, "min_version_year", None),
            sample_strategy=getattr(args, "sample_strategy", "head") or "head",
            families=getattr(args, "families", None),
            workers=int(getattr(args, "workers", DEFAULT_WORKERS) or DEFAULT_WORKERS),
        )
    except NZBenchmarkSelectionError as exc:
        raise SystemExit(f"nz-corpus replay-chain-corpus: {exc}") from exc

    if getattr(args, "json", False):
        print(
            json.dumps(
                report.to_jsonable(summary_only=getattr(args, "summary_only", False)),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    summary = report.summary()
    selection = summary["selection_context"]
    print(
        f"families={'+'.join(summary['families_requested'])} "
        f"replay_claims={summary['replay_claims']} "
        f"(experimental all-families chain replay, partial coverage)"
    )
    print(
        f"works_attempted={summary['works_attempted']} "
        f"works_scored={summary['works_scored']} "
        f"works_errored={summary['works_errored']} "
        f"works_no_final_version={summary['works_no_final_version']} "
        f"available_work_count={selection['available_work_count']} "
        f"max_works={selection['max_works']} "
        f"truncated_by_max_works={selection['truncated_by_max_works']}"
    )
    print(
        f"workers={summary['workers']} wall_clock_seconds={summary['wall_clock_seconds']}"
    )
    if summary["errored_work_ids"]:
        print(f"errored_work_ids={summary['errored_work_ids']}")
    print("--- HONEST CORPUS E2E: final stable-combined similarity distribution ---")
    _print_distribution("stable_combined", report.similarity_distribution())
    _print_distribution("raw_combined   ", report.raw_similarity_distribution())
    _print_distribution("shared_mean    ", report.shared_mean_distribution())
    print("--- per-family totals (enumerated / applied / skipped / oracle_agree) ---")
    for family in CHAIN_FAMILY_ORDER:
        stat = summary["per_family_totals"].get(family)
        if stat is None:
            continue
        print(
            f"  {family:<13} enumerated={stat['enumerated']} applied={stat['applied']} "
            f"skipped={stat['skipped']} "
            f"oracle_agree={stat['oracle_agreements']}/{stat['oracle_total']}"
        )
    print("--- ranked skip/extraction-cap census (the next-lever, by count) ---")
    if not summary["skip_cap_census"]:
        print("  (no typed skips across the corpus)")
    for entry in summary["skip_cap_census"]:
        print(
            f"  {entry['count']:>6}  {entry['bucket']}  "
            f"exemplars={entry['exemplar_work_ids']}"
        )
    print(f"total_divergences={summary['total_divergences']}")

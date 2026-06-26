"""lawvm bench -j nz — New Zealand actual (canonical) replay benchmark.

This scores the **actual replay** surface (``build_archived_work_actual_replay``),
the single NZ surface where ``replay_claims`` is ``True``. For each curated work
it materializes every transition whose change window is fully dry-run-verified,
then scores the materialized after-tree against the archived on-or-after oracle
with a FI-style **dual similarity** (text + tree), reusing the existing NZ
primitives — it invents no new metric:

* text similarity  : ``core.evidence_support.section_similarity`` over the union
  of node paths (the ``combined_similarity`` track of ``chain_replay``).
* tree similarity  : the path-Jaccard / stable combined-similarity track of
  ``chain_replay._similarity_point`` (positional/identity churn collapsed).

Both are computed by reusing ``chain_replay._similarity_point`` directly on the
materialized after-document vs the re-parsed archived oracle document.

Reporting is deliberately **multi-lane** and never flattens coverage into the
similarity headline. A high similarity over a tiny replayed fraction must not
look like broad success, so the score is always reported ALONGSIDE the replayed
fraction. The coverage lanes are kept separate and prominent:

* transitions actually replayed (the strict, fail-closed count),
* transitions fail-closed-blocked (refused: a declared transition whose window
  contained an unverified op), split into:
    - verification-failed   : a proof was formed but it disagreed with the oracle
      (or perturbed a neighbour), and
    - refusal-blocked       : the dry-run kernel declined to even form a
      candidate (payload-not-extractable / anchor-not-derivable / target absent),
* families requested but not attempted (e.g. a structural family with no
  operation surface).

A REPORT-ONLY extra lane, **would-replay-if-refusals-ignored**, exposes the
conservatism: how many additional transitions WOULD materialize if the dry-run
REFUSAL-blocked ops in a window (ops the kernel declined to even form a candidate
for) were treated as not-declared rather than blocking, while
verification-failed ops keep blocking. This NEVER materializes those transitions
and NEVER weakens the fail-closed contract — the strict replayed count above is
untouched. It only counts the windows that are blocked *solely* by refusals.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lawvm.core.bench_contract import BenchUnitResult

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.corpus_cache import (
    active_corpus_run_cache,
    corpus_run_cache,
)
from lawvm.new_zealand.actual_replay import (
    NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES,
    NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
    NZActualReplayReport,
    build_actual_replay,
)
from lawvm.new_zealand.chain_replay import _similarity_point
from lawvm.new_zealand.dry_run import _parse_archived_version
from lawvm.new_zealand.effect_candidates import (
    build_archived_work_effect_candidate_preflight,
)
from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface
from lawvm.new_zealand.version_diff import archived_xml_version_change_window

_DEFAULT_DB = Path("data/nz_legislation.farchive")
_DEFAULT_CORPUS = Path("data/nz/bench_corpus.csv")
_SMOKE_CORPUS = Path("data/nz/bench_corpus_smoke.csv")

# Refusal rule ids whose presence in a window means a proof WAS formed but failed
# verification (oracle disagreed or a neighbour was perturbed). These keep
# blocking even in the would-replay-if-refusals-ignored lane — only the kernel's
# "declined to form a candidate" refusals are the conservatism that lane exposes.
_VERIFICATION_FAILED_REFUSAL_RULE_IDS = frozenset(
    {
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
    }
)


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------


# Cheap size proxies, in priority order, that the curated corpus CSV exposes
# before any scoring happens. Per-work scoring cost scales with the number of
# amendment operations / history witnesses (each drives a transition replay +
# oracle parse), so these are a faithful, free load-balancing key.
_SIZE_PROXY_COLUMNS = ("n_amendment_operations", "n_history_witnesses")


def _load_corpus(csv_path: Path, max_works: int | None) -> list[str]:
    """Load work_ids from a curated NZ bench corpus CSV (in file order)."""
    return [wid for wid, _ in _load_corpus_with_size(csv_path, max_works)]


def _load_corpus_with_size(
    csv_path: Path, max_works: int | None
) -> list[tuple[str, int]]:
    """Load ``(work_id, size_proxy)`` pairs in CSV (file) order.

    ``size_proxy`` is a cheap, pre-scoring estimate of per-work scoring cost
    read straight from the corpus CSV (``n_amendment_operations``, falling back
    to ``n_history_witnesses``). It is used only for largest-work-first
    execution ordering and never affects which works are scored: ``max_works``
    is still applied to the CSV (file) order here, BEFORE any reordering.

    When no size column is present the proxy is 0 for every work, so callers
    fall back to a stable (CSV-order) execution order.
    """
    pairs: list[tuple[str, int]] = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "work_id" not in reader.fieldnames:
            raise ValueError(
                f"NZ bench corpus {csv_path} is missing a 'work_id' column "
                f"(found {reader.fieldnames})"
            )
        size_col = next(
            (c for c in _SIZE_PROXY_COLUMNS if c in reader.fieldnames), None
        )
        for row in reader:
            wid = (row.get("work_id") or "").strip()
            if not wid:
                continue
            size = 0
            if size_col is not None:
                raw = (row.get(size_col) or "").strip()
                if raw:
                    try:
                        size = int(raw)
                    except ValueError:
                        size = 0
            pairs.append((wid, size))
    if max_works is not None and max_works > 0:
        pairs = pairs[:max_works]
    return pairs


def _execution_order(pairs: list[tuple[str, int]]) -> list[str]:
    """Largest-work-first execution order for load balancing.

    Sorts the (already ``--max-works``-selected) works by descending size proxy
    so long-running works start first and workers do not idle on a long tail.
    Ties (and the no-size-column case, where every proxy is 0) break on the
    original CSV index, keeping the order stable and deterministic. This only
    affects the ORDER work is dispatched to the pool — the scored set and the
    final aggregate are unchanged because results are re-sorted to CSV order
    before aggregation.
    """
    indexed = list(enumerate(pairs))
    indexed.sort(key=lambda iw: (-iw[1][1], iw[0]))
    return [wid for _, (wid, _) in indexed]


# ---------------------------------------------------------------------------
# Per-work scoring
# ---------------------------------------------------------------------------


@dataclass
class _TransitionScore:
    amendment_date_iso: str
    text_similarity: float
    tree_similarity: float
    tree_similarity_stable: float
    path_jaccard: float
    slice_node_count: int
    slice_agreements: int
    ops: int


@dataclass
class _WorkResult:
    work_id: str
    families: tuple[str, ...]
    # Open-ended: "OK" or a runtime ``EXC:<type>:<msg>`` string captured at the
    # bench boundary, so this stays typed-open rather than a closed Literal.
    work_status: str
    transitions_replayed: int
    transitions_refused: int
    ops_replayed: int
    slice_nodes: int
    slice_agreements: int
    all_slices_agree: bool
    # Coverage lanes (kept separate — never folded into the similarity headline).
    refusals_verification_failed: int
    refusals_refusal_blocked: int
    families_not_attempted: int
    # REPORT-ONLY conservatism lane.
    would_replay_if_refusals_ignored: int
    # Dual similarity over actually-replayed transitions.
    text_similarity: float
    tree_similarity: float
    tree_similarity_stable: float
    # Oracle agreement BY typed residual family (agreement vs source-honest
    # disagreement vs replay_bug) — never just a similarity number.
    residual_family_counts: dict[str, int] = field(default_factory=dict)
    transition_scores: list[_TransitionScore] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "families": list(self.families),
            "work_status": self.work_status,
            "transitions_replayed": self.transitions_replayed,
            "transitions_refused": self.transitions_refused,
            "ops_replayed": self.ops_replayed,
            "target_slice_nodes": self.slice_nodes,
            "target_slice_agreements": self.slice_agreements,
            "all_slices_agree": self.all_slices_agree,
            "refusals_verification_failed": self.refusals_verification_failed,
            "refusals_refusal_blocked": self.refusals_refusal_blocked,
            "families_not_attempted": self.families_not_attempted,
            "would_replay_if_refusals_ignored": self.would_replay_if_refusals_ignored,
            "text_similarity": round(self.text_similarity, 6),
            "tree_similarity": round(self.tree_similarity, 6),
            "tree_similarity_stable": round(self.tree_similarity_stable, 6),
            "residual_family_counts": dict(sorted(self.residual_family_counts.items())),
            "transition_scores": [
                {
                    "amendment_date_iso": ts.amendment_date_iso,
                    "text_similarity": round(ts.text_similarity, 6),
                    "tree_similarity": round(ts.tree_similarity, 6),
                    "tree_similarity_stable": round(ts.tree_similarity_stable, 6),
                    "path_jaccard": round(ts.path_jaccard, 6),
                    "slice_node_count": ts.slice_node_count,
                    "slice_agreements": ts.slice_agreements,
                    "ops": ts.ops,
                }
                for ts in self.transition_scores
            ],
        }


def _classify_refusal_lanes(report: NZActualReplayReport) -> tuple[int, int, int]:
    """Split refusals into (verification_failed, refusal_blocked, would_replay).

    * verification_failed : a proof was formed but disagreed / perturbed.
    * refusal_blocked     : the dry-run kernel declined to form a candidate
      (NOT_DRY_RUN_VERIFIED carrying a ``dry_run_refusal_rule_id``), distinct
      from sibling-blocked verified ops (NOT_DRY_RUN_VERIFIED without one).
    * would_replay        : transitions blocked SOLELY by refusal-blocked ops —
      they carry no verification-failed refusal, so they WOULD materialize if the
      kernel's "declined to form a candidate" conservatism were dropped. This is
      report-only and never materializes anything.
    """

    verification_failed = 0
    refusal_blocked = 0
    # Per change window (amendment date): does it carry any verification-failed
    # refusal, and does it carry any refusal-blocked op?
    window_has_verification_failed: dict[str, bool] = {}
    window_has_refusal_blocked: dict[str, bool] = {}
    for refusal in report.refusals:
        date = refusal.amendment_date_iso
        window_has_verification_failed.setdefault(date, False)
        window_has_refusal_blocked.setdefault(date, False)
        if refusal.rule_id in _VERIFICATION_FAILED_REFUSAL_RULE_IDS:
            verification_failed += 1
            window_has_verification_failed[date] = True
        elif refusal.rule_id == NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID:
            # NOT_DRY_RUN_VERIFIED is overloaded: a kernel refusal carries a
            # dry_run_refusal_rule_id; a sibling-blocked verified op does not.
            if "dry_run_refusal_rule_id" in refusal.detail:
                refusal_blocked += 1
                window_has_refusal_blocked[date] = True
            # else: sibling-blocked verified op — not a refusal-blocked op; it does
            # not by itself make a window "blocked solely by refusals".
        else:
            # Any other (window-level) refusal rule id is treated as a hard block
            # that the conservatism lane does not get to ignore.
            window_has_verification_failed[date] = True

    would_replay = sum(
        1
        for date, has_refusal in window_has_refusal_blocked.items()
        if has_refusal and not window_has_verification_failed.get(date, False)
    )
    return verification_failed, refusal_blocked, would_replay


def _score_one_work(archive: Any, work_id: str, db_path: Path) -> _WorkResult:
    """Build the actual-replay report for one work and score its transitions."""

    try:
        preflight = build_archived_work_effect_candidate_preflight(db_path, work_id)
        surface = build_archived_work_operation_surface(db_path, work_id)
        report = build_actual_replay(
            archive,
            work_id=work_id,
            preflight=preflight,
            families=NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES,
            surface=surface,
        )
    except Exception as exc:  # surface the failure loudly per-work, never silent
        return _WorkResult(
            work_id=work_id,
            families=(),
            work_status=f"EXC:{type(exc).__name__}:{str(exc)[:80]}",
            transitions_replayed=0,
            transitions_refused=0,
            ops_replayed=0,
            slice_nodes=0,
            slice_agreements=0,
            all_slices_agree=False,
            refusals_verification_failed=0,
            refusals_refusal_blocked=0,
            families_not_attempted=0,
            would_replay_if_refusals_ignored=0,
            text_similarity=0.0,
            tree_similarity=0.0,
            tree_similarity_stable=0.0,
            residual_family_counts={"error": 1},
        )

    summary = report.summary()
    verification_failed, refusal_blocked, would_replay = _classify_refusal_lanes(report)

    transition_scores: list[_TransitionScore] = []
    parsed_cache: dict[str, Any] = {}
    for transition in report.transitions:
        change_window = archived_xml_version_change_window(
            archive, work_id=work_id, version_date=transition.amendment_date_iso
        )
        oracle_version = change_window.on_or_after
        text_sim = 0.0
        tree_sim = 0.0
        tree_sim_stable = 0.0
        path_jaccard = 0.0
        if oracle_version is not None:
            oracle_doc = _parse_archived_version(archive, oracle_version, parsed_cache)
            if oracle_doc is not None:
                point = _similarity_point(
                    transition.materialized_after,
                    oracle_doc,
                    oracle_version,
                    transitions_applied=0,
                    repeals_applied=0,
                    repeals_skipped=0,
                )
                text_sim = point.combined_similarity
                tree_sim = point.path_jaccard
                tree_sim_stable = point.combined_similarity_stable
                path_jaccard = point.path_jaccard
        transition_scores.append(
            _TransitionScore(
                amendment_date_iso=transition.amendment_date_iso,
                text_similarity=text_sim,
                tree_similarity=tree_sim,
                tree_similarity_stable=tree_sim_stable,
                path_jaccard=path_jaccard,
                slice_node_count=transition.target_slice_node_count,
                slice_agreements=transition.target_slice_agreements,
                ops=len(transition.mutations),
            )
        )

    n = len(transition_scores)
    text_mean = sum(ts.text_similarity for ts in transition_scores) / n if n else 0.0
    tree_mean = sum(ts.tree_similarity for ts in transition_scores) / n if n else 0.0
    tree_stable_mean = (
        sum(ts.tree_similarity_stable for ts in transition_scores) / n if n else 0.0
    )

    return _WorkResult(
        work_id=work_id,
        families=tuple(summary["families"]),
        work_status="OK",
        transitions_replayed=summary["transitions_replayed"],
        transitions_refused=summary["transitions_refused"],
        ops_replayed=summary["ops_replayed"],
        slice_nodes=summary["target_slice_nodes"],
        slice_agreements=summary["target_slice_agreements"],
        all_slices_agree=summary["all_slices_agree"],
        refusals_verification_failed=verification_failed,
        refusals_refusal_blocked=refusal_blocked,
        families_not_attempted=len(report.families_not_attempted),
        would_replay_if_refusals_ignored=would_replay,
        text_similarity=text_mean,
        tree_similarity=tree_mean,
        tree_similarity_stable=tree_stable_mean,
        residual_family_counts={
            str(family): int(count)
            for family, count in (summary.get("residual_family_counts") or {}).items()
        },
        transition_scores=transition_scores,
    )


# ---------------------------------------------------------------------------
# Unified cross-jurisdiction bench contract — New Zealand adapter
#
# Re-house the existing NZ per-work numbers into a contract BenchUnitResult
# without changing them. NZ computes a discrete target-slice agreement count
# (reconcilable) alongside a continuous text similarity:
# - structural_err = 1 - slice_agreements / slice_nodes (discrete agreement)
# - text_err       = 1 - text_similarity (continuous)
# residue is the disagreeing slice nodes, which is non-zero iff the agreement
# ratio is below 1 — so the structural error reconciles. The continuous tree
# similarity is not the reconciling axis (see notes/UNIFIED_BENCH_CONTRACT.md).
# ---------------------------------------------------------------------------


def nz_bench_unit_result(result: "_WorkResult") -> "BenchUnitResult":
    """Map an NZ ``_WorkResult`` onto a contract ``BenchUnitResult``."""
    from lawvm.core.bench_contract import BenchStatus, BenchUnitResult

    if result.work_status != "OK":
        # "EXC:..." — a genuine failure.
        return BenchUnitResult(
            unit_id=result.work_id,
            status=BenchStatus.CRASH,
            witnesses=(result.work_status,),
        )
    if result.slice_nodes <= 0:
        # No target slice nodes materialized — nothing to score against.
        return BenchUnitResult(unit_id=result.work_id, status=BenchStatus.NO_TRUTH)

    disagreements = max(0, result.slice_nodes - result.slice_agreements)
    structural_err = disagreements / result.slice_nodes
    residue: dict[str, int] = {}
    if disagreements > 0:
        residue["slice_disagreement"] = disagreements
        # Fold in the typed oracle residual families for triage. Only when the
        # structural axis is non-zero, so the reconciliation invariant (no
        # phantom residue at zero error) holds.
        for family, count in result.residual_family_counts.items():
            if count and family != "agreement":
                residue[f"oracle_{family}"] = int(count)
    text_err = 1.0 - result.text_similarity
    text_err = min(1.0, max(0.0, text_err))
    return BenchUnitResult(
        unit_id=result.work_id,
        status=BenchStatus.SCORED,
        structural_err=structural_err,
        text_err=text_err,
        residue_buckets=residue,
    )


def _register_nz_bench_comparator() -> None:
    from lawvm.core.bench_comparator_registry import register_bench_comparator

    register_bench_comparator("nz", nz_bench_unit_result)


_register_nz_bench_comparator()


# ---------------------------------------------------------------------------
# Parallel scoring (one farchive handle per worker process)
# ---------------------------------------------------------------------------
#
# Each work is scored independently: ``_score_one_work`` opens nothing that is
# shared across works except the read-only farchive handle, and an open Farchive
# handle is NOT safe to share across processes. So workers open their OWN handle
# once per process (in the initializer) and reuse it for every work they score.
# Determinism: the parallel path scores the exact same works and each
# ``_WorkResult`` is a pure function of its ``work_id``; results are re-sorted to
# CSV order by the caller before aggregation, so the aggregate + JSON are
# byte-identical to the serial path regardless of completion scheduling.

# Hard pool-size ceiling. Each worker holds its own open farchive handle and the
# per-work parse caches, so pool size multiplies resident memory; cap it to stay
# under the WSL2 memory ceiling regardless of host core count.
_MAX_WORKERS = 8

# Worker-process globals, set once by the initializer (never pickled per task).
_WORKER_ARCHIVE: Any = None
_WORKER_DB_PATH: Path | None = None
# Each worker process holds one run-scoped parse/archive cache open for its whole
# lifetime so the same archived version XML is parsed at most once per worker
# (across the preflight, surface, dry-run families, change-window, and oracle
# re-parse). The cache is purely a performance layer (frozen, input-addressed
# parses) so per-work results stay byte-identical to the uncached path.
_WORKER_CACHE_CM: Any = None


def _default_workers() -> int:
    """Default worker count: min(16, cpu-2), then clamped to the memory cap."""
    cpus = os.cpu_count() or 2
    return max(1, min(_MAX_WORKERS, min(16, cpus - 2)))


def _resolve_workers(requested: int | None) -> int:
    """Resolve the requested ``--parallel`` value into an effective pool size.

    ``None`` / ``0`` -> auto default (min(16, cpu-2), capped at the memory
    ceiling); ``1`` -> serial; ``N`` -> clamped to the memory ceiling.
    """
    if requested is None or requested <= 0:
        return _default_workers()
    return max(1, min(_MAX_WORKERS, requested))


def _worker_init(db_path_str: str) -> None:
    """Process-pool initializer: open this worker's own farchive handle once.

    Also enters a run-scoped parse/archive cache held open for the worker's
    lifetime, so the worker's farchive handle is the shared cache wrapper and
    each archived version XML parses at most once per worker. The cache only
    memoizes pure, (locator, version_id)-addressed parses, so results stay
    byte-identical to the uncached path.
    """
    global _WORKER_ARCHIVE, _WORKER_DB_PATH, _WORKER_CACHE_CM
    _WORKER_CACHE_CM = corpus_run_cache()
    _WORKER_CACHE_CM.__enter__()
    _WORKER_DB_PATH = Path(db_path_str)
    _WORKER_ARCHIVE = open_farchive(_WORKER_DB_PATH)


def _score_one_work_worker(work_id: str) -> _WorkResult:
    """Picklable per-work task: score using this worker's own farchive handle."""
    assert _WORKER_ARCHIVE is not None and _WORKER_DB_PATH is not None, (
        "NZ bench worker not initialized (farchive handle missing)"
    )
    result = _score_one_work(_WORKER_ARCHIVE, work_id, _WORKER_DB_PATH)
    # Bound per-worker memory: distinct works share ~no archived XML, so drop the
    # parsed-document memo between works while keeping the shared archive handle.
    cache = active_corpus_run_cache()
    if cache is not None:
        cache.reset_parsed()
    return result


def _score_corpus(
    *,
    db_path: Path,
    ordered_work_ids: list[str],
    workers: int,
    quiet: bool,
    total: int,
    t0: float,
) -> list[_WorkResult]:
    """Score every work, parallel when ``workers > 1``, else serial.

    ``ordered_work_ids`` is the largest-work-first execution order. Progress
    lines stream as results COMPLETE (so they may be out of CSV order — each
    line carries the work_id so it stays readable). Returns results in
    completion order; the caller sorts them to CSV order for the aggregate.
    """
    results: list[_WorkResult] = []

    def _stream(done: int, result: _WorkResult) -> None:
        if quiet:
            return
        elapsed = time.time() - t0
        print(
            _format_progress_line(
                done=done, total=total, elapsed=elapsed, result=result
            ),
            file=sys.stderr,
            flush=True,
        )

    if workers <= 1:
        # Share one parsed-document/archive cache across the whole serial run so
        # each archived version XML is decompressed + lxml-parsed at most once
        # across the preflight, operation surface, every dry-run family, the
        # change-window lookups, and the oracle re-parse — instead of re-parsing
        # the same before/oracle/latest versions per call. Pure performance: the
        # cache only memoizes frozen, (locator, version_id)-addressed parses, so
        # each ``_WorkResult`` is byte-identical to the uncached path. The shared
        # archive handle returned by ``open_farchive`` is the cache wrapper while
        # the run context is active.
        with corpus_run_cache():
            run_cache = active_corpus_run_cache()
            archive = open_farchive(db_path)
            try:
                for i, work_id in enumerate(ordered_work_ids):
                    result = _score_one_work(archive, work_id, db_path)
                    results.append(result)
                    _stream(i + 1, result)
                    # Distinct works share ~no archived XML, so drop the parsed
                    # memo between works to bound the working set while keeping
                    # the run-shared archive handle (same pattern as the NZ
                    # dry-run/chain-replay corpus runners).
                    if run_cache is not None:
                        run_cache.reset_parsed()
            finally:
                archive.close()
        return results

    from concurrent.futures import as_completed

    from lawvm.tools._worker_pool import managed_executor

    with managed_executor(
        workers,
        initializer=_worker_init,
        initargs=(str(db_path),),
    ) as pool:
        futures = {
            pool.submit(_score_one_work_worker, work_id): work_id
            for work_id in ordered_work_ids
        }
        for done, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            _stream(done, result)
    return results


# ---------------------------------------------------------------------------
# Aggregate + report
# ---------------------------------------------------------------------------


def _aggregate(results: list[_WorkResult]) -> dict[str, Any]:
    ok = [r for r in results if r.work_status == "OK"]
    errs = [r for r in results if r.work_status != "OK"]
    replayed_works = [r for r in ok if r.transitions_replayed > 0]

    total_transitions_replayed = sum(r.transitions_replayed for r in ok)
    total_transitions_refused = sum(r.transitions_refused for r in ok)
    total_would_replay = sum(r.would_replay_if_refusals_ignored for r in ok)
    total_verification_failed = sum(r.refusals_verification_failed for r in ok)
    total_refusal_blocked = sum(r.refusals_refusal_blocked for r in ok)
    total_families_not_attempted = sum(r.families_not_attempted for r in ok)
    total_ops_replayed = sum(r.ops_replayed for r in ok)
    total_slice_nodes = sum(r.slice_nodes for r in ok)
    total_slice_agreements = sum(r.slice_agreements for r in ok)

    # Similarity is the mean over actually-replayed TRANSITIONS (node-weighted by
    # transition count via per-work means averaged over replayed works). Reported
    # ALWAYS next to the replayed fraction so a high score over a tiny replayed
    # slice cannot masquerade as broad success.
    all_scores = [ts for r in replayed_works for ts in r.transition_scores]
    nt = len(all_scores)
    text_sim = sum(ts.text_similarity for ts in all_scores) / nt if nt else 0.0
    tree_sim = sum(ts.tree_similarity for ts in all_scores) / nt if nt else 0.0
    tree_sim_stable = (
        sum(ts.tree_similarity_stable for ts in all_scores) / nt if nt else 0.0
    )

    # The replayed fraction: actually-replayed transitions over all declared
    # transitions (replayed + fail-closed-refused). This is the coverage the
    # similarity score is computed over.
    declared_transitions = total_transitions_replayed + total_transitions_refused
    replayed_fraction = (
        total_transitions_replayed / declared_transitions if declared_transitions else 0.0
    )
    # If the refusal-only blocked transitions were treated as not-declared, what
    # fraction could replay? Report-only — does NOT change the strict count.
    hypothetical_replayed = total_transitions_replayed + total_would_replay
    would_replay_fraction = (
        hypothetical_replayed / declared_transitions if declared_transitions else 0.0
    )

    slice_agreement_ratio = (
        total_slice_agreements / total_slice_nodes if total_slice_nodes else 0.0
    )

    # Oracle agreement BY typed residual family across the slice: agreement vs
    # source-honest disagreement (accepted_non_executable_frontier /
    # temporal_mismatch / source_footing_gap) vs genuine replay_bug. Reported as a
    # separate lane so agreement is counted by family, not just a similarity number.
    residual_family_counts: dict[str, int] = {}
    for r in ok:
        for family, count in r.residual_family_counts.items():
            residual_family_counts[family] = residual_family_counts.get(family, 0) + int(count)

    return {
        "n_works": len(results),
        "n_ok": len(ok),
        "n_errors": len(errs),
        "n_works_with_replay": len(replayed_works),
        # Coverage lanes — kept separate and prominent.
        "transitions_replayed": total_transitions_replayed,
        "transitions_refused": total_transitions_refused,
        "declared_transitions": declared_transitions,
        "replayed_fraction": round(replayed_fraction, 6),
        "refusals_verification_failed": total_verification_failed,
        "refusals_refusal_blocked": total_refusal_blocked,
        "families_not_attempted": total_families_not_attempted,
        # REPORT-ONLY conservatism lane.
        "would_replay_if_refusals_ignored": total_would_replay,
        "hypothetical_replayed_if_refusals_ignored": hypothetical_replayed,
        "would_replay_fraction": round(would_replay_fraction, 6),
        # Replay yield.
        "ops_replayed": total_ops_replayed,
        "target_slice_nodes": total_slice_nodes,
        "target_slice_agreements": total_slice_agreements,
        "slice_agreement_ratio": round(slice_agreement_ratio, 6),
        # Dual similarity over actually-replayed transitions.
        "transitions_scored": nt,
        "text_similarity": round(text_sim, 6),
        "tree_similarity": round(tree_sim, 6),
        "tree_similarity_stable": round(tree_sim_stable, 6),
        # Oracle agreement by typed residual family (not just a similarity number).
        "oracle_agreement_residual_family_counts": dict(sorted(residual_family_counts.items())),
    }


def _format_progress_line(
    *, done: int, total: int, elapsed: float, result: _WorkResult
) -> str:
    """One informative progress line per work, matching the uk_bench house style.

    ``  [done/total] work_id<padded>  <key result> (Ns) status=...``

    The key-result fragment is the same multi-lane signal the final report keeps
    separate: transitions replayed/refused, the slice agreement, and the dual
    text/tree similarity over the actually-replayed transitions. Error/empty
    works carry a typed status instead of a similarity number so they read
    cleanly as they scroll.
    """

    if result.work_status != "OK":
        result_fragment = "ERROR"
    elif result.transitions_replayed == 0:
        # No transition materialized — surface WHY via the coverage lanes rather
        # than a misleading 0% similarity (nothing was scored).
        result_fragment = (
            f"repl=0 refused={result.transitions_refused} "
            f"(no replay; would+={result.would_replay_if_refusals_ignored})"
        )
    else:
        result_fragment = (
            f"repl={result.transitions_replayed} "
            f"refused={result.transitions_refused} "
            f"slice={result.slice_agreements}/{result.slice_nodes} "
            f"text={result.text_similarity:.0%} tree={result.tree_similarity:.0%}"
        )
    return (
        f"  [{done}/{total}] {result.work_id:<30} "
        f"{result_fragment} ({elapsed:.0f}s) status={result.work_status}"
    )


def _render_unified_summary(results: list[_WorkResult], label: str) -> None:
    """Print the shared cross-jurisdiction headline via the bench contract.

    NZ scores two axes — structural (target-slice agreement) and text
    (text similarity over actually-replayed transitions) — so the worst-of
    headline is the binding (max) of the two. The detailed bespoke NZ report
    (``_print_report``), which keeps the per-lane structural/text/tree numbers
    and coverage lanes separate, follows and is preserved in full.
    """
    from lawvm.core.bench_aggregate import render_summary

    unit_results = [nz_bench_unit_result(r) for r in results]
    for line in render_summary(unit_results, label, jurisdiction="nz"):
        print(line)


def _print_report(results: list[_WorkResult], agg: dict[str, Any], corpus: Path) -> None:
    print(f"\n=== NZ actual-replay bench: {corpus} ===")
    print(
        f"Works: {agg['n_works']} (ok={agg['n_ok']}, errors={agg['n_errors']}, "
        f"with-replay={agg['n_works_with_replay']})"
    )
    print("\nCoverage lanes (kept separate — NOT folded into the similarity score):")
    print(
        f"  transitions actually replayed : {agg['transitions_replayed']}"
        f"  /  declared {agg['declared_transitions']}"
        f"  (replayed fraction = {agg['replayed_fraction']:.1%})"
    )
    print(f"  transitions fail-closed-blocked: {agg['transitions_refused']}")
    print(f"    - verification-failed ops    : {agg['refusals_verification_failed']}")
    print(f"    - refusal-blocked ops        : {agg['refusals_refusal_blocked']}")
    print(f"  families requested not attempted: {agg['families_not_attempted']}")
    print(
        f"  would-replay-if-refusals-ignored: +{agg['would_replay_if_refusals_ignored']}"
        f"  -> {agg['hypothetical_replayed_if_refusals_ignored']} "
        f"({agg['would_replay_fraction']:.1%}) [REPORT-ONLY; strict count unchanged]"
    )
    print("\nReplay yield:")
    print(f"  ops replayed                  : {agg['ops_replayed']}")
    print(
        f"  target slice agreements       : {agg['target_slice_agreements']}"
        f"/{agg['target_slice_nodes']}  ({agg['slice_agreement_ratio']:.1%})"
    )
    print("\nDual similarity over ACTUALLY-REPLAYED transitions "
          f"(N={agg['transitions_scored']} transitions; "
          f"replayed fraction {agg['replayed_fraction']:.1%}):")
    print(f"  text similarity               : {agg['text_similarity']:.1%}")
    print(f"  tree similarity (path jaccard): {agg['tree_similarity']:.1%}")
    print(f"  tree similarity (stable)      : {agg['tree_similarity_stable']:.1%}")
    print("\nOracle agreement by typed residual family (not just a similarity number):")
    print(f"  {agg['oracle_agreement_residual_family_counts']}")

    replayed_works = sorted(
        (r for r in results if r.transitions_replayed > 0),
        key=lambda r: -r.transitions_replayed,
    )
    if replayed_works:
        print(f"\nTop replayed works (showing {min(15, len(replayed_works))}):")
        for r in replayed_works[:15]:
            print(
                f"  {r.work_id:28s} repl={r.transitions_replayed:3d} "
                f"refused={r.transitions_refused:3d} "
                f"ops={r.ops_replayed:3d} "
                f"slice={r.slice_agreements}/{r.slice_nodes} "
                f"text={r.text_similarity:.1%} tree={r.tree_similarity:.1%} "
                f"would+={r.would_replay_if_refusals_ignored}"
            )
    errs = [r for r in results if r.work_status != "OK"]
    if errs:
        print(f"\nErrors ({len(errs)}):")
        for r in errs[:15]:
            print(f"  {r.work_id}: {r.work_status}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    db_path = Path(getattr(args, "db", None) or _DEFAULT_DB)
    if not db_path.exists():
        print(f"NZ farchive not found: {db_path}", file=sys.stderr)
        raise SystemExit(2)

    explicit_corpus = getattr(args, "corpus", None)
    if explicit_corpus:
        corpus = Path(explicit_corpus)
    elif getattr(args, "smoke", False):
        corpus = _SMOKE_CORPUS
    else:
        corpus = _DEFAULT_CORPUS
    if not corpus.exists():
        print(f"NZ bench corpus not found: {corpus}", file=sys.stderr)
        raise SystemExit(2)

    max_works = getattr(args, "max_works", None)
    # --max-works is applied to the CSV (file) order FIRST, so it always selects
    # the same set of works regardless of the execution ordering below.
    corpus_pairs = _load_corpus_with_size(corpus, max_works)
    if not corpus_pairs:
        print(f"NZ bench corpus {corpus} contained no work_ids", file=sys.stderr)
        raise SystemExit(2)

    # Canonical CSV order: results are re-sorted into this order before the
    # aggregate + JSON so those outputs are deterministic regardless of worker
    # count or completion scheduling.
    csv_order = [wid for wid, _ in corpus_pairs]
    csv_rank = {wid: i for i, wid in enumerate(csv_order)}
    # Largest-work-first execution order for load balancing (does NOT change the
    # scored set; results are sorted back to CSV order afterwards).
    ordered_work_ids = _execution_order(corpus_pairs)

    workers = _resolve_workers(getattr(args, "parallel", None))

    quiet = getattr(args, "json", False) and not getattr(args, "output_json", None)

    corpus_label = "smoke" if corpus == _SMOKE_CORPUS else "full"
    if max_works is not None and max_works > 0:
        corpus_label += f"; capped at first {max_works}"
    total = len(csv_order)
    if not quiet:
        print(
            f"NZ actual-replay bench: scoring {total} works "
            f"from {corpus} ({corpus_label}; workers={workers}, "
            f"largest-work-first)",
            file=sys.stderr,
            flush=True,
        )
        if corpus == _DEFAULT_CORPUS and max_works is None:
            print(
                "  (full corpus is slow; use --smoke or --max-works N for a quick run)",
                file=sys.stderr,
                flush=True,
            )

    t0 = time.time()
    results = _score_corpus(
        db_path=db_path,
        ordered_work_ids=ordered_work_ids,
        workers=workers,
        quiet=quiet,
        total=total,
        t0=t0,
    )

    # Deterministic aggregate + JSON: re-sort completion-order results into the
    # canonical CSV order before aggregating or emitting.
    results.sort(key=lambda r: csv_rank[r.work_id])

    agg = _aggregate(results)

    output_json = getattr(args, "output_json", None)
    if getattr(args, "json", False) or output_json:
        payload = {
            "jurisdiction": "nz",
            "bench_kind": "actual_replay",
            "corpus": str(corpus),
            "summary": agg,
            "works": [r.to_jsonable() for r in results],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if output_json:
            Path(output_json).write_text(text + "\n", encoding="utf-8")
            print(f"NZ bench JSON written: {output_json}", file=sys.stderr)
        if getattr(args, "json", False):
            print(text)
        if not getattr(args, "json", False):
            _render_unified_summary(results, str(corpus))
            _print_report(results, agg, corpus)
        return

    _render_unified_summary(results, str(corpus))
    _print_report(results, agg, corpus)

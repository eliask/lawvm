"""Stable cross-cycle replay-coverage north-star for NZ dry-run families.

The per-family corpus scoreboard in :mod:`lawvm.new_zealand.dry_run_corpus`
measures dry-run-oracle agreement, but its *coverage fraction* historically used
a candidate-derived denominator (the count of emitted/blocked candidate rows of
a family). That denominator GROWS as extraction improves, so the fraction can
fall even as real progress is made — it is not comparable across cycles.

This module pins the denominator to the ground truth instead: the count of
AMENDMENT OPERATION WITNESSES from provision history notes, surfaced by
:mod:`lawvm.new_zealand.operation_surface`. A history-note witness is a fact of
the source XML — it does not move when candidate extraction or the dry-run
kernels improve. So:

    per-family coverage = (operation-witnesses of that family whose dry-run op
                           AGREES with the oracle)
                          / (total operation-witnesses of that family)

This number can only rise as extraction and kernels improve — a true progress
metric and a stable cross-cycle baseline.

History-note operation families are partitioned into pinned buckets:

- ``supported`` families have a dry-run kernel today (repeal, text_replace,
  replace, insert) and contribute to the combined north-star numerator/denominator;
- ``frontier`` families are executable amendment operations we do not yet support
  — the explicit remaining work, ordered by witness count. The frontier is
  currently empty of pinned families; ``inserted``/``added`` moved to the
  supported ``insert`` family this cycle and ``replaced``/``substituted`` to the
  supported ``replace`` family, so any newly-surfaced unsupported family appears
  here via the unbucketed default;
- ``non_executable`` families are not replayable structural mutations by design
  (brought-into-force / editorial / expired) — reported separately, never as a
  coverage miss;
- ``unclassified`` families are history notes whose operation word we could not
  classify — reported separately, never as coverage or frontier.

The combined north-star is the true "% of NZ amendment operations we can
replay-and-oracle-confirm" = supported-family agreeing / supported-family
witnesses.

Determinism: works are processed in the given order, family buckets are pinned
constants, and all tallies are emitted sorted by key. No clock, no randomness.
``replay_claims`` stays ``False`` and the dry-run kernels are not changed — this
is measurement only.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from lawvm.new_zealand.corpus_cache import active_corpus_run_cache, corpus_run_cache
from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    build_archived_work_dry_run_repeal,
)
from lawvm.new_zealand.operation_surface import build_archived_work_operation_surface

# Pinned bucket vocabulary. These are stable cross-cycle category names.
NZ_NORTH_STAR_BUCKET_SUPPORTED = "supported"
NZ_NORTH_STAR_BUCKET_FRONTIER = "frontier"
NZ_NORTH_STAR_BUCKET_NON_EXECUTABLE = "non_executable"
NZ_NORTH_STAR_BUCKET_UNCLASSIFIED = "unclassified"

# Pinned dry-run families and the history-note operation family each one covers.
# A history-note ``operation_family`` is the ground-truth amendment word from the
# provision history note; it does not grow when candidate extraction improves.
#
# - repeal       <- "repealed"  history notes (whole-provision repeal)
# - text_replace <- "amended"   history notes (in-provision text substitution;
#                                NZ records text edits under "amended", not under
#                                "replaced"/"substituted")
# - replace      <- "replaced"/"substituted" history notes (whole-provision
#                                structural substitution: the target node's
#                                subtree is swapped for the amending act's
#                                <amend> payload, oracle-checked by subtree match)
# - insert       <- "inserted"/"added" history notes (whole-provision structural
#                                insert: a new node is ADDED next to a derived
#                                anchor sibling — the new node's content comes from
#                                the amending act's <amend> payload, the anchor is
#                                derived from the suffix-letter label, oracle-checked
#                                by the new node being present with matching content)
NZ_NORTH_STAR_SUPPORTED_FAMILIES: dict[str, tuple[str, ...]] = {
    "repeal": ("repealed",),
    "text_replace": ("amended",),
    "replace": ("replaced", "substituted"),
    "insert": ("inserted", "added"),
}

# The dry-run scope that drives each supported family's kernel.
_SUPPORTED_FAMILY_SCOPE: dict[str, str] = {
    "repeal": NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPEAL,
    "text_replace": NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_TEXT_REPLACE,
    "replace": NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_REPLACE,
    "insert": NZ_DRY_RUN_SCOPE_SELECTED_FAMILY_INSERT,
}

# Executable amendment operations we do not yet support: the remaining frontier.
# ``inserted``/``added`` moved to the supported ``insert`` family this cycle
# (structural whole-provision insert kernel). The frontier is now empty of pinned
# families; any unbucketed real family surfaces here loudly via the unbucketed
# default.
NZ_NORTH_STAR_FRONTIER_FAMILIES: tuple[str, ...] = ()

# Not replayable structural mutations by design — never a coverage miss.
NZ_NORTH_STAR_NON_EXECUTABLE_FAMILIES: tuple[str, ...] = (
    "brought into force",
    "editorial change",
    "expired",
)

# History notes whose operation word the surface could not classify.
NZ_NORTH_STAR_UNCLASSIFIED_FAMILIES: tuple[str, ...] = (
    "__missing__",
    "__unclassified__",
)

# Map every known history-note operation family to its pinned bucket. Built once
# from the constants above so the partition is exhaustive and order-independent.
_FAMILY_TO_BUCKET: dict[str, str] = {}
for _family in (history for families in NZ_NORTH_STAR_SUPPORTED_FAMILIES.values() for history in families):
    _FAMILY_TO_BUCKET[_family] = NZ_NORTH_STAR_BUCKET_SUPPORTED
for _family in NZ_NORTH_STAR_FRONTIER_FAMILIES:
    _FAMILY_TO_BUCKET[_family] = NZ_NORTH_STAR_BUCKET_FRONTIER
for _family in NZ_NORTH_STAR_NON_EXECUTABLE_FAMILIES:
    _FAMILY_TO_BUCKET[_family] = NZ_NORTH_STAR_BUCKET_NON_EXECUTABLE
for _family in NZ_NORTH_STAR_UNCLASSIFIED_FAMILIES:
    _FAMILY_TO_BUCKET[_family] = NZ_NORTH_STAR_BUCKET_UNCLASSIFIED

# A history-note operation family that is real (a fact of the source) but not in
# any pinned bucket. It is bucketed here distinctly so a future surface family
# surfaces loudly as frontier-to-classify rather than being silently dropped.
NZ_NORTH_STAR_UNBUCKETED_FAMILY_BUCKET = NZ_NORTH_STAR_BUCKET_FRONTIER

# History-note witness row id embedded in a dry-run proof op_id (e.g.
# ``nz:act_public_2022_77:nz-opw-110:repeal``). Used to map an agreeing proof
# back to the ground-truth witness it confirms.
_WITNESS_ROW_ID_RE = re.compile(r"(nz-opw-\d+)")


def _bucket_for_family(history_family: str) -> str:
    return _FAMILY_TO_BUCKET.get(history_family, NZ_NORTH_STAR_UNBUCKETED_FAMILY_BUCKET)


def _supported_family_for_history_family(history_family: str) -> str | None:
    for supported_family, history_families in NZ_NORTH_STAR_SUPPORTED_FAMILIES.items():
        if history_family in history_families:
            return supported_family
    return None


def _proof_witness_row_id(op_id: str) -> str:
    match = _WITNESS_ROW_ID_RE.search(op_id)
    return match.group(1) if match else op_id


@dataclass(frozen=True)
class NZWorkNorthStarCensus:
    """One work's ground-truth family census plus its agreeing witness ids.

    ``history_family_counts`` is the stable denominator source: how many history
    notes of each operation family the work owns. ``agreeing_witness_row_ids`` is
    the set of witness row ids whose dry-run op (in the family's selected scope)
    agreed with the oracle — the numerator, mapped back onto the ground truth.
    """

    work_id: str
    history_family_counts: dict[str, int]
    # supported_family -> sorted tuple of witness row ids that agreed.
    agreeing_witness_row_ids: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class NZDryRunNorthStarReport:
    """Combined, stable replay-coverage north-star across supported families."""

    db_path: str
    work_censuses: tuple[NZWorkNorthStarCensus, ...]
    requested_work_ids: tuple[str, ...] = ()
    selected_work_ids: tuple[str, ...] = ()

    def _aggregate_history_family_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for census in self.work_censuses:
            for family, count in census.history_family_counts.items():
                counts[family] = counts.get(family, 0) + count
        return counts

    def _aggregate_agreeing_counts(self) -> dict[str, int]:
        # Distinct agreeing witness rows per supported family. A proof maps 1:1
        # to a witness row, so the count is bounded by the family denominator and
        # never double-counts a witness.
        counts: dict[str, int] = {family: 0 for family in NZ_NORTH_STAR_SUPPORTED_FAMILIES}
        for census in self.work_censuses:
            for family, row_ids in census.agreeing_witness_row_ids.items():
                counts[family] = counts.get(family, 0) + len(set(row_ids))
        return counts

    def summary(self) -> dict[str, Any]:
        history_counts = self._aggregate_history_family_counts()
        agreeing_counts = self._aggregate_agreeing_counts()

        bucket_totals: dict[str, int] = {
            NZ_NORTH_STAR_BUCKET_SUPPORTED: 0,
            NZ_NORTH_STAR_BUCKET_FRONTIER: 0,
            NZ_NORTH_STAR_BUCKET_NON_EXECUTABLE: 0,
            NZ_NORTH_STAR_BUCKET_UNCLASSIFIED: 0,
        }
        bucket_family_counts: dict[str, dict[str, int]] = {bucket: {} for bucket in bucket_totals}
        for family, count in history_counts.items():
            bucket = _bucket_for_family(family)
            bucket_totals[bucket] += count
            bucket_family_counts[bucket][family] = count

        total_amendment_operation_witnesses = sum(history_counts.values())

        # Per supported family: agreeing / total against the PINNED denominator.
        per_family: dict[str, dict[str, Any]] = {}
        supported_total = 0
        supported_agreeing = 0
        for supported_family in sorted(NZ_NORTH_STAR_SUPPORTED_FAMILIES):
            history_families = NZ_NORTH_STAR_SUPPORTED_FAMILIES[supported_family]
            family_total = sum(history_counts.get(history, 0) for history in history_families)
            family_agreeing = agreeing_counts.get(supported_family, 0)
            supported_total += family_total
            supported_agreeing += family_agreeing
            per_family[supported_family] = {
                "history_families": list(history_families),
                "operation_witnesses": family_total,
                "dry_run_agreeing": family_agreeing,
                "coverage_fraction": (family_agreeing / family_total) if family_total else None,
            }

        combined_fraction = (supported_agreeing / supported_total) if supported_total else None

        return {
            "db_path": self.db_path,
            "report_kind": "dry_run_north_star",
            "works_attempted": len(self.work_censuses),
            # The denominator universe: every history-note amendment operation.
            "total_amendment_operation_witnesses": total_amendment_operation_witnesses,
            # Buckets of the pinned partition (exhaustive, disjoint).
            "non_executable_by_design_witnesses": bucket_totals[NZ_NORTH_STAR_BUCKET_NON_EXECUTABLE],
            "unclassified_witnesses": bucket_totals[NZ_NORTH_STAR_BUCKET_UNCLASSIFIED],
            "remaining_frontier_witnesses": bucket_totals[NZ_NORTH_STAR_BUCKET_FRONTIER],
            "supported_family_witnesses": bucket_totals[NZ_NORTH_STAR_BUCKET_SUPPORTED],
            # The north-star: agreeing supported-family witnesses over all
            # supported-family witnesses. Monotone-rising as the loop improves.
            "supported_family_dry_run_agreeing": supported_agreeing,
            "combined_coverage_fraction": combined_fraction,
            "per_family": per_family,
            # The frontier, broken down so the largest unsupported family orders
            # which kernel to build next.
            "remaining_frontier_family_counts": dict(sorted(bucket_family_counts[NZ_NORTH_STAR_BUCKET_FRONTIER].items())),
            "non_executable_family_counts": dict(
                sorted(bucket_family_counts[NZ_NORTH_STAR_BUCKET_NON_EXECUTABLE].items())
            ),
            "unclassified_family_counts": dict(sorted(bucket_family_counts[NZ_NORTH_STAR_BUCKET_UNCLASSIFIED].items())),
            "history_family_counts": dict(sorted(history_counts.items())),
            # Measurement only: never authorizes or claims actual replay.
            "replay_claims": False,
            "dry_run_claims": True,
            "actual_replay_agreements": 0,
            "actual_replay_blocking_rule_id": NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
        }

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "jurisdiction": "nz",
            "report_kind": "dry_run_north_star",
            "truth_claim": (
                "history_note_operation_witness_denominator_vs_dry_run_oracle_agreement_not_actual_replay"
            ),
            "replay_claims": False,
            "dry_run_claims": True,
            "actual_replay_blocking_rule_id": NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
            "selected_work_ids_count": len(self.selected_work_ids or tuple(c.work_id for c in self.work_censuses)),
            "summary": self.summary(),
        }


def build_nz_work_north_star_census(db_path: Path, work_id: str) -> NZWorkNorthStarCensus:
    """Census one work: ground-truth families + per-family agreeing witness ids.

    The denominator (history-note family counts) comes from the operation
    surface — the ground truth that does not move with extraction. The numerator
    runs each supported dry-run family's selected-family scope and records the
    witness row ids whose proof agreed with the oracle. The dry-run kernels are
    consumed unchanged; replay is never authorized.
    """

    surface = build_archived_work_operation_surface(db_path, work_id)
    history_family_counts: dict[str, int] = {}
    for row in surface.rows:
        history_family_counts[row.operation_family] = history_family_counts.get(row.operation_family, 0) + 1

    agreeing: dict[str, tuple[str, ...]] = {}
    for supported_family, scope in sorted(_SUPPORTED_FAMILY_SCOPE.items()):
        report = build_archived_work_dry_run_repeal(db_path, work_id, scope=scope)
        row_ids = sorted(
            {_proof_witness_row_id(proof.op_id) for proof in report.proofs if proof.oracle_match == "agrees"}
        )
        agreeing[supported_family] = tuple(row_ids)

    return NZWorkNorthStarCensus(
        work_id=work_id,
        history_family_counts=history_family_counts,
        agreeing_witness_row_ids=agreeing,
    )


def _progress_enabled() -> bool:
    # Opt-in stderr progress for long full-corpus runs. Never touches stdout, so
    # the report output is unaffected.
    return bool(os.environ.get("NZ_NORTH_STAR_PROGRESS")) and sys.stderr.isatty()


def _census_with_progress(db_path: Path, selected: list[str]) -> Iterator[NZWorkNorthStarCensus]:
    """Census each selected work in order, emitting optional stderr progress.

    Yielding in the given order keeps the aggregate byte-identical to the serial
    comprehension it replaces; the progress line goes to stderr only.
    """

    show_progress = _progress_enabled()
    total = len(selected)
    started = time.monotonic()
    cache = active_corpus_run_cache()
    for index, work_id in enumerate(selected, start=1):
        census = build_nz_work_north_star_census(db_path, work_id)
        # Within-work parses are shared across families and lookups; drop them
        # before the next work to bound memory (distinct works share ~no XML).
        if cache is not None:
            cache.reset_parsed()
        if show_progress and (index == total or index % 25 == 0):
            elapsed = time.monotonic() - started
            rate = index / elapsed if elapsed > 0 else 0.0
            eta = (total - index) / rate if rate > 0 else 0.0
            print(
                f"north-star {index}/{total} works  elapsed={elapsed:.0f}s  eta={eta:.0f}s",
                file=sys.stderr,
                flush=True,
            )
        yield census


def build_nz_dry_run_north_star_report(
    db_path: Path,
    *,
    work_ids: tuple[str, ...],
    max_works: int | None = None,
) -> NZDryRunNorthStarReport:
    """Run the combined north-star over a work population.

    ``work_ids`` is the work population (typically a curated bench corpus). The
    works are processed in the given order; ``max_works`` truncates the head.
    """

    requested = tuple(dict.fromkeys(work_ids))
    selected = list(requested)
    if max_works is not None:
        selected = selected[: max(max_works, 0)]

    # Share one parsed-document/archive cache across every work in the run so each
    # archived version XML is parsed at most once across families and works. This
    # is a pure performance layer: the per-work census results are byte-identical
    # to the uncached path (frozen, input-addressed parses), and the works are
    # still processed in the given deterministic order.
    with corpus_run_cache():
        censuses = tuple(_census_with_progress(db_path, selected))
    return NZDryRunNorthStarReport(
        db_path=str(db_path),
        work_censuses=censuses,
        requested_work_ids=requested,
        selected_work_ids=tuple(selected),
    )


def main(args: Any) -> None:
    import json

    work_ids = tuple(getattr(args, "work_id", None) or ())
    corpus_path = getattr(args, "corpus", None)
    if corpus_path:
        from lawvm.new_zealand.bench_corpus import NZBenchCorpusError, read_corpus_work_ids

        try:
            corpus_work_ids = read_corpus_work_ids(Path(corpus_path))
        except NZBenchCorpusError as exc:
            raise SystemExit(f"nz-corpus dry-run-north-star: {exc}") from exc
        if not work_ids:
            work_ids = corpus_work_ids

    if not work_ids:
        raise SystemExit("nz-corpus dry-run-north-star: provide --work-id or --corpus")

    report = build_nz_dry_run_north_star_report(
        Path(args.db),
        work_ids=work_ids,
        max_works=getattr(args, "max_works", None),
    )

    if getattr(args, "json", False):
        print(json.dumps(report.to_jsonable(), ensure_ascii=False, indent=2))
        return

    summary = report.summary()
    frac = summary["combined_coverage_fraction"]
    frac_text = f"{frac:.4f}" if frac is not None else "n/a"
    print(f"report_kind={summary['report_kind']} works_attempted={summary['works_attempted']}")
    print(
        f"total_amendment_operation_witnesses={summary['total_amendment_operation_witnesses']} "
        f"non_executable_by_design={summary['non_executable_by_design_witnesses']} "
        f"unclassified={summary['unclassified_witnesses']} "
        f"remaining_frontier={summary['remaining_frontier_witnesses']} "
        f"supported_family_witnesses={summary['supported_family_witnesses']}"
    )
    print(
        f"NORTH_STAR supported_family_dry_run_agreeing={summary['supported_family_dry_run_agreeing']} "
        f"combined_coverage_fraction={frac_text}"
    )
    for family in sorted(summary["per_family"]):
        stats = summary["per_family"][family]
        family_frac = stats["coverage_fraction"]
        family_frac_text = f"{family_frac:.4f}" if family_frac is not None else "n/a"
        print(
            f"per_family\t{family}\thistory={stats['history_families']}\t"
            f"agreeing={stats['dry_run_agreeing']}\ttotal={stats['operation_witnesses']}\t"
            f"coverage={family_frac_text}"
        )
    print(f"remaining_frontier_family_counts={summary['remaining_frontier_family_counts']}")
    print(f"non_executable_family_counts={summary['non_executable_family_counts']}")
    if summary["unclassified_family_counts"]:
        print(f"unclassified_family_counts={summary['unclassified_family_counts']}")
    print(f"actual_replay_blocking_rule_id={summary['actual_replay_blocking_rule_id']}")

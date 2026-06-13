"""Corpus-scale NZ dry-run repeal aggregator and agreement report.

This surface proves that the single-canary dry-run repeal surface in
:mod:`lawvm.new_zealand.dry_run` generalizes beyond one hand-picked work. It

- selects a representative modern ``act_public`` population with the existing
  benchmark sampler (:func:`lawvm.new_zealand.benchmark.select_benchmark_work_ids`),
  so the corpus runner never reinvents sampling or silently falls back to the
  lexicographic head of ancient imperial acts;
- runs :func:`lawvm.new_zealand.dry_run.build_dry_run_repeal` per selected work
  (read-only against the archive);
- aggregates the per-work :class:`~lawvm.new_zealand.dry_run.NZDryRunReport`
  proofs and refusals into a corpus agreement rate and a typed residual
  taxonomy (counts per ``oracle_match`` family + refusals per ``rule_id``, with
  a few ``work_id:op_id`` exemplars per family).

It inherits the boring discipline of the single-work surface: it never enables
actual replay, never mutates the archive, and never claims canonical corpus
state. ``replay_claims`` stays ``False`` everywhere. It only consumes the
single-work surface; it does not change its semantics.

The aggregation is deterministic: works are processed in the sorted selection
order produced by the sampler, and all tallies are emitted sorted by key. No
clock, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.benchmark import NZBenchmarkSelectionError, select_benchmark_work_ids
from lawvm.new_zealand.dry_run import (
    NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
    NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID,
    NZ_DRY_RUN_SCOPE_COMPLETE_SET,
    NZDryRunReport,
    build_archived_work_dry_run_repeal,
    scope_from_arg,
)


# Number of ``work_id:op_id`` (or ``work_id``) exemplars retained per tally key.
_EXEMPLAR_LIMIT = 5
# Number of selected work ids echoed back in the selection context sample.
_SELECTION_WORK_ID_SAMPLE_LIMIT = 50

_READY_PREFLIGHT_STATUS = "ready_for_dry_run_replay"


@dataclass(frozen=True)
class NZDryRunRepealCorpusReport:
    """Aggregate dry-run repeal report across a selected NZ work population.

    Per-work reports are retained so callers can drill into a single surprising
    residual. The summary projects the corpus agreement rate and the residual /
    refusal taxonomy. Like the single-work surface, this never authorizes
    actual replay.
    """

    db_path: str
    work_reports: tuple[NZDryRunReport, ...]
    requested_work_ids: tuple[str, ...] = ()
    selected_work_ids: tuple[str, ...] = ()
    available_work_count: int = 0
    max_works: int | None = None
    scope: str = NZ_DRY_RUN_SCOPE_COMPLETE_SET

    def works_with_ready_preflight(self) -> tuple[NZDryRunReport, ...]:
        return tuple(report for report in self.work_reports if report.preflight_status == _READY_PREFLIGHT_STATUS)

    def selection_context(self) -> dict[str, Any]:
        selected = self.selected_work_ids or tuple(report.work_id for report in self.work_reports)
        requested = self.requested_work_ids
        base_count = len(requested) if requested else self.available_work_count
        selected_sample = selected[:_SELECTION_WORK_ID_SAMPLE_LIMIT]
        requested_sample = requested[:_SELECTION_WORK_ID_SAMPLE_LIMIT]
        return {
            "available_work_count": self.available_work_count,
            "requested_work_count": len(requested),
            "requested_work_ids_sample": list(requested_sample),
            "requested_work_ids_omitted": max(len(requested) - len(requested_sample), 0),
            "selected_work_count": len(selected),
            "selected_work_ids_sample": list(selected_sample),
            "selected_work_ids_omitted": max(len(selected) - len(selected_sample), 0),
            "max_works": self.max_works,
            # No silent truncation: state the cap and whether it actually bit.
            "truncated_by_max_works": self.max_works is not None and len(selected) < base_count,
        }

    def _repeal_witness_coverage(self) -> dict[str, Any]:
        """Corpus-wide repeal-family replay-coverage scoreboard.

        The denominator is every repeal operation-witness in the sampled
        corpus (dry-run-eligible, not-in-scope, or still blocked). The key loop
        metric is the fraction of those witnesses that are dry-run-agreeing.
        Witnesses that are not agreeing are accounted for by typed reason
        (residual, per-op refusal, not-in-scope, or blocked), never hidden.

        This is only meaningful in the selected_family_repeal scope, where each
        work carries a repeal-witness census. In complete_set scope no work
        carries the census, so the fraction is reported as unavailable rather
        than misleadingly high.
        """

        total_repeal_witnesses = 0
        repeal_in_scope = 0
        not_in_scope_reason_counts: dict[str, int] = {}
        per_op_refusals = 0
        dry_run_agreeing = 0
        dry_run_residual = 0
        census_available = False

        for report in self.work_reports:
            completeness = report.scope_completeness
            if completeness is None:
                continue
            census_available = True
            total_repeal_witnesses += completeness.total_repeal_operation_witnesses
            repeal_in_scope += completeness.repeal_witnesses_in_scope
            for reason, count in completeness.repeal_witnesses_not_in_scope_reason_counts.items():
                not_in_scope_reason_counts[reason] = not_in_scope_reason_counts.get(reason, 0) + count
            for proof in report.proofs:
                if proof.oracle_match == "agrees":
                    dry_run_agreeing += 1
                else:
                    dry_run_residual += 1
            for refusal in report.refusals:
                # The whole-work "no replayable repeal candidate" refusal is
                # keyed by the work id, not a repeal witness, so it must not be
                # subtracted from the repeal-witness denominator.
                if refusal.rule_id == NZ_DRY_RUN_REFUSED_NO_REPEAL_CANDIDATE_RULE_ID:
                    continue
                per_op_refusals += 1

        not_in_scope_total = sum(not_in_scope_reason_counts.values())
        blocked_or_unready = max(
            total_repeal_witnesses - dry_run_agreeing - dry_run_residual - per_op_refusals - not_in_scope_total,
            0,
        )
        coverage_fraction = (
            (dry_run_agreeing / total_repeal_witnesses) if (census_available and total_repeal_witnesses) else None
        )
        return {
            "census_available": census_available,
            "total_repeal_operation_witnesses": total_repeal_witnesses,
            "repeal_witnesses_in_scope": repeal_in_scope,
            "dry_run_agreeing": dry_run_agreeing,
            "dry_run_residual": dry_run_residual,
            "per_op_refused": per_op_refusals,
            "not_in_scope": not_in_scope_total,
            "not_in_scope_reason_counts": dict(sorted(not_in_scope_reason_counts.items())),
            "blocked_or_unready": blocked_or_unready,
            # The loop metric: fraction of all repeal operation-witnesses
            # corpus-wide that are dry-run-agreeing.
            "dry_run_agreeing_fraction": coverage_fraction,
        }

    def summary(self) -> dict[str, Any]:
        ready_reports = self.works_with_ready_preflight()

        total_ops = 0
        total_agreements = 0
        total_residuals = 0
        neighbors_unchanged_all = True

        # Residual taxonomy keyed by the proof oracle_match family, with the
        # rule id carried alongside the count and a few exemplars.
        oracle_match_counts: dict[str, int] = {}
        oracle_match_rule_ids: dict[str, set[str]] = {}
        oracle_match_exemplars: dict[str, list[str]] = {}
        residual_oracle_match_counts: dict[str, int] = {}
        residual_exemplars: dict[str, list[str]] = {}

        # Refusal taxonomy keyed by rule_id, with exemplars.
        refusal_rule_counts: dict[str, int] = {}
        refusal_exemplars: dict[str, list[str]] = {}

        for report in self.work_reports:
            total_ops += len(report.proofs)
            for proof in report.proofs:
                if not proof.neighbors_unchanged:
                    neighbors_unchanged_all = False
                family = proof.oracle_match or "__none__"
                exemplar = f"{report.work_id}:{proof.op_id}"
                oracle_match_counts[family] = oracle_match_counts.get(family, 0) + 1
                oracle_match_rule_ids.setdefault(family, set()).add(proof.oracle_match_rule_id)
                _append_exemplar(oracle_match_exemplars, family, exemplar)
                if proof.oracle_match == "agrees":
                    total_agreements += 1
                else:
                    total_residuals += 1
                    residual_oracle_match_counts[family] = residual_oracle_match_counts.get(family, 0) + 1
                    _append_exemplar(residual_exemplars, family, exemplar)
            for refusal in report.refusals:
                rule_id = refusal.rule_id or "__none__"
                refusal_rule_counts[rule_id] = refusal_rule_counts.get(rule_id, 0) + 1
                _append_exemplar(refusal_exemplars, rule_id, f"{report.work_id}:{refusal.op_id}")

        agreement_rate = (total_agreements / total_ops) if total_ops else None

        repeal_coverage = self._repeal_witness_coverage()

        return {
            "db_path": self.db_path,
            "scope": self.scope,
            "selection_context": self.selection_context(),
            "operation_family": "repeal",
            "works_attempted": len(self.work_reports),
            "works_with_ready_preflight": len(ready_reports),
            "works_with_dry_run_proofs": sum(1 for report in self.work_reports if report.proofs),
            "total_repeal_ops_dry_run": total_ops,
            "dry_run_oracle_agreements": total_agreements,
            "dry_run_oracle_residuals": total_residuals,
            "dry_run_oracle_agreement_rate": agreement_rate,
            "repeal_witness_coverage": repeal_coverage,
            "neighbors_unchanged_all": neighbors_unchanged_all,
            "oracle_match_family_counts": _sorted_int_map(oracle_match_counts),
            "oracle_match_family_rule_ids": {
                family: sorted(rule_ids) for family, rule_ids in sorted(oracle_match_rule_ids.items())
            },
            "oracle_match_family_exemplars": _sorted_list_map(oracle_match_exemplars),
            "residual_oracle_match_family_counts": _sorted_int_map(residual_oracle_match_counts),
            "residual_family_exemplars": _sorted_list_map(residual_exemplars),
            "refusal_rule_counts": _sorted_int_map(refusal_rule_counts),
            "refusal_rule_exemplars": _sorted_list_map(refusal_exemplars),
            "preflight_status_counts": _sorted_int_map(
                _tally(report.preflight_status or "__none__" for report in self.work_reports)
            ),
            # Dry-run agreement only. Actual replay is never claimed here.
            "replay_claims": False,
            "actual_replay_agreements": 0,
            "dry_run_claims": True,
            "actual_replay_blocking_rule_id": NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
        }

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "dry_run_repeal_corpus",
            "truth_claim": "dry_run_after_tree_vs_archived_on_or_after_xml_not_actual_replay",
            "replay_claims": False,
            "dry_run_claims": True,
            "scope": self.scope,
            "actual_replay_blocking_rule_id": NZ_DRY_RUN_NOT_REPLAY_AUTHORIZED_RULE_ID,
            "selection_context": self.selection_context(),
            "summary": self.summary(),
        }
        if summary_only:
            return payload
        payload["works"] = [report.to_jsonable(summary_only=True) for report in self.work_reports]
        return payload


def build_nz_dry_run_repeal_corpus_report(
    db_path: Path,
    *,
    work_ids: tuple[str, ...] = (),
    max_works: int | None = None,
    work_id_prefix: str = "",
    min_version_year: int | None = None,
    sample_strategy: str = "head",
    scope: str = NZ_DRY_RUN_SCOPE_COMPLETE_SET,
) -> NZDryRunRepealCorpusReport:
    """Run the dry-run repeal surface across a selected NZ work population.

    When ``work_ids`` is given, those works are run directly (the
    prefix/year/strategy filters only shape the archive-wide default
    population, never an explicit list); ``max_works`` still truncates the
    explicit list. Otherwise the representative sampler selects the population.

    The per-work runs are serial and processed in the deterministic selection
    order, so the aggregate tallies are reproducible from farchive bytes.
    """

    archive = open_farchive(db_path)
    try:
        archived_work_ids = _archived_work_ids(archive)
        available_work_count = len(archived_work_ids)
        requested_work_ids = tuple(dict.fromkeys(work_ids))
        if requested_work_ids:
            selected = list(requested_work_ids)
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

    # Each per-work dry-run opens its own archive handle (the single-work
    # surface owns its preflight build + archive lifecycle). Serial iteration
    # over the deterministic selection keeps the aggregate reproducible.
    reports = tuple(build_archived_work_dry_run_repeal(db_path, work_id, scope=scope) for work_id in selected)

    return NZDryRunRepealCorpusReport(
        db_path=str(db_path),
        work_reports=reports,
        requested_work_ids=requested_work_ids,
        selected_work_ids=tuple(selected),
        available_work_count=available_work_count,
        max_works=max_works,
        scope=scope,
    )


def _archived_work_ids(archive: Any) -> tuple[str, ...]:
    from lawvm.new_zealand.benchmark import _archived_work_max_version_year

    return tuple(_archived_work_max_version_year(archive))


def _append_exemplar(store: dict[str, list[str]], key: str, value: str) -> None:
    bucket = store.setdefault(key, [])
    if len(bucket) < _EXEMPLAR_LIMIT and value not in bucket:
        bucket.append(value)


def _tally(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _sorted_int_map(counts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(counts.items()))


def _sorted_list_map(store: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key: list(store[key]) for key in sorted(store)}


def main(args: Any) -> None:
    import json

    work_ids = tuple(getattr(args, "work_id", None) or ())
    corpus_path = getattr(args, "corpus", None)
    if corpus_path:
        from lawvm.new_zealand.bench_corpus import NZBenchCorpusError, read_corpus_work_ids

        try:
            corpus_work_ids = read_corpus_work_ids(Path(corpus_path))
        except NZBenchCorpusError as exc:
            raise SystemExit(f"nz-corpus dry-run-corpus: {exc}") from exc
        # An explicit --work-id list still wins; otherwise the curated corpus
        # supplies the work population (replacing the sampler).
        if not work_ids:
            work_ids = corpus_work_ids

    try:
        report = build_nz_dry_run_repeal_corpus_report(
            Path(args.db),
            work_ids=work_ids,
            max_works=args.max_works,
            work_id_prefix=getattr(args, "work_id_prefix", "") or "",
            min_version_year=getattr(args, "min_version_year", None),
            sample_strategy=getattr(args, "sample_strategy", "head") or "head",
            scope=scope_from_arg(getattr(args, "scope", None)),
        )
    except NZBenchmarkSelectionError as exc:
        raise SystemExit(f"nz-corpus dry-run-corpus: {exc}") from exc

    if args.json:
        print(
            json.dumps(
                report.to_jsonable(summary_only=args.summary_only),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    summary = report.summary()
    selection = summary["selection_context"]
    rate = summary["dry_run_oracle_agreement_rate"]
    rate_text = f"{rate:.4f}" if rate is not None else "n/a"
    print(f"scope={summary['scope']}")
    print(
        f"works_attempted={summary['works_attempted']} "
        f"works_with_ready_preflight={summary['works_with_ready_preflight']} "
        f"works_with_dry_run_proofs={summary['works_with_dry_run_proofs']} "
        f"total_repeal_ops_dry_run={summary['total_repeal_ops_dry_run']} "
        f"dry_run_oracle_agreements={summary['dry_run_oracle_agreements']} "
        f"dry_run_oracle_residuals={summary['dry_run_oracle_residuals']} "
        f"agreement_rate={rate_text} "
        f"neighbors_unchanged_all={summary['neighbors_unchanged_all']}"
    )
    print(
        f"selected_work_count={selection['selected_work_count']} "
        f"available_work_count={selection['available_work_count']} "
        f"max_works={selection['max_works']} "
        f"truncated_by_max_works={selection['truncated_by_max_works']}"
    )
    coverage = summary["repeal_witness_coverage"]
    if coverage["census_available"]:
        frac = coverage["dry_run_agreeing_fraction"]
        frac_text = f"{frac:.4f}" if frac is not None else "n/a"
        print(
            f"repeal_witness_coverage total_repeal_witnesses={coverage['total_repeal_operation_witnesses']} "
            f"dry_run_agreeing={coverage['dry_run_agreeing']} "
            f"dry_run_agreeing_fraction={frac_text} "
            f"residual={coverage['dry_run_residual']} "
            f"per_op_refused={coverage['per_op_refused']} "
            f"not_in_scope={coverage['not_in_scope']} "
            f"blocked_or_unready={coverage['blocked_or_unready']}"
        )
        if coverage["not_in_scope_reason_counts"]:
            print(f"repeal_not_in_scope_reason_counts={coverage['not_in_scope_reason_counts']}")
    print(f"preflight_status_counts={summary['preflight_status_counts']}")
    print(f"oracle_match_family_counts={summary['oracle_match_family_counts']}")
    if summary["residual_oracle_match_family_counts"]:
        print(f"residual_oracle_match_family_counts={summary['residual_oracle_match_family_counts']}")
        print(f"residual_family_exemplars={summary['residual_family_exemplars']}")
    if summary["refusal_rule_counts"]:
        print(f"refusal_rule_counts={summary['refusal_rule_counts']}")
        print(f"refusal_rule_exemplars={summary['refusal_rule_exemplars']}")
    print(f"actual_replay_blocking_rule_id={summary['actual_replay_blocking_rule_id']}")
    if args.summary_only:
        return
    for report_row in report.work_reports:
        row_summary = report_row.summary()
        print(
            f"WORK\t{report_row.work_id}\t{report_row.preflight_status}\t"
            f"ops={row_summary['operations_dry_run']}\t"
            f"agree={row_summary['dry_run_oracle_agreements']}\t"
            f"resid={row_summary['dry_run_oracle_residuals']}\t"
            f"refused={row_summary['operations_refused']}\t"
            f"oracle={row_summary['oracle_match_counts']}"
        )

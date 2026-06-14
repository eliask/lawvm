#!/usr/bin/env python3
"""EE consolidation-error candidate surface — standalone entrypoint.

Mines the EE replay/consistency corpus for divergences where LawVM is plausibly
RIGHT and the official Riigi Teataja consolidation (terviktekst) is plausibly
WRONG, and emits a DETERMINISTIC ranked findings report (the adoption wedge).

It reuses, never re-implements:
  * replay_ee_to_pit(...)        — the replay-vs-consolidation divergence stream
  * build_ee_residual_summary    — the post-hoc residual adjudication
  * consolidation_error_candidates(...) — the tiering / ranking carrier

Tiering (honest, never overclaiming):
  STRONG : divergence adjudicated to a consolidation-side error bucket
           (source_oracle_drift / oracle_correction_notice) — ranked first.
  TRIAGE : divergence with no residual record, flagged
           `unadjudicated_needs_review` — surfaced for review, NOT asserted as a
           consolidation error.

Usage (from LawVM/ dir):
    # whole known-residual corpus slice (default), deterministic JSON to stdout
    uv run python scripts/ee_consolidation_error_candidates.py --json

    # a single pair
    uv run python scripts/ee_consolidation_error_candidates.py \
        --base-id 118092025007 --oracle-id 106122024009

    # human-readable report
    uv run python scripts/ee_consolidation_error_candidates.py

    # restrict the corpus slice
    uv run python scripts/ee_consolidation_error_candidates.py --limit 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lawvm.estonia.consolidation_error_candidates import (  # noqa: E402
    ConsolidationErrorCandidateReport,
    consolidation_error_candidates,
    report_to_jsonable,
)
from lawvm.estonia.residual_inventory import (  # noqa: E402
    list_known_ee_residual_inventories,
)


def _resolve_as_of(oracle_id: str, archive) -> str:
    """Resolve the comparison date from the oracle terviktekst effective date."""
    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml

    oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
    return extract_effective_date(oracle_xml) or "9999-12-31"


def _corpus_slice(limit: int | None) -> list[tuple[str, str]]:
    """The default corpus slice: known-residual EE (base, oracle) pairs, sorted.

    Sorted so the producer is deterministic regardless of dict iteration order.
    """
    pairs = sorted(
        (inv.base_id, inv.oracle_id)
        for inv in list_known_ee_residual_inventories()
    )
    if limit is not None:
        pairs = pairs[:limit]
    return pairs


def _run_pair(base_id: str, oracle_id: str, archive) -> ConsolidationErrorCandidateReport:
    as_of = _resolve_as_of(oracle_id, archive)
    return consolidation_error_candidates(
        base_id=base_id,
        as_of=as_of,
        oracle_id=oracle_id,
        archive=archive,
    )


def _print_report(report: ConsolidationErrorCandidateReport) -> None:
    print()
    print(f"=== EE consolidation-error candidates: {report.base_id} -> {report.oracle_id} ===")
    print(f"  statute : {report.statute_title[:60]}")
    print(f"  compare : {report.comparison_class}")
    print(f"  adjudication present: {'yes' if report.has_residual_adjudication else 'no'}")
    print(f"  STRONG (consolidation-wrong-in-force): {report.strong_count}")
    print(f"  TRIAGE (unadjudicated_needs_review)  : {report.triage_count}")

    if report.strong_candidates:
        print("\n  STRONG candidates (adjudicated consolidation-side error):")
        for c in report.strong_candidates:
            _print_candidate(c)
    if report.triage_candidates:
        print("\n  TRIAGE candidates (unadjudicated — surfaced for review, NOT asserted):")
        for c in report.triage_candidates:
            _print_candidate(c)
    if not report.strong_candidates and not report.triage_candidates:
        print("\n  (no candidates)")


def _print_candidate(c) -> None:
    act = c.amending_act or "?"
    rule = c.witness_rule_id or "-"
    print(f"    [{c.divergence_type:<20}] {c.address}  [{c.residual_bucket}]")
    print(f"      amending act: {act}   witness rule: {rule}")
    if c.evidence.replay_snippet:
        print(f"      replay: {c.evidence.replay_snippet!r}")
    if c.evidence.consolidated_snippet:
        print(f"      conslt: {c.evidence.consolidated_snippet!r}")
    if c.residual_evidence:
        print(f"      note  : {c.residual_evidence}")


def _aggregate_jsonable(reports: list[ConsolidationErrorCandidateReport]) -> dict:
    """Deterministic aggregate JSON across the corpus slice."""
    report_dicts = [report_to_jsonable(r) for r in reports]
    return {
        "pair_count": len(reports),
        "strong_total": sum(r.strong_count for r in reports),
        "triage_total": sum(r.triage_count for r in reports),
        "reports": report_dicts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-id", help="EE base aktViide (single-pair mode)")
    parser.add_argument("--oracle-id", help="EE oracle aktViide (single-pair mode)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the corpus slice to the first N known pairs (sorted).",
    )
    parser.add_argument("--json", action="store_true", help="Emit deterministic JSON.")
    args = parser.parse_args()

    from lawvm.estonia.fetch import open_rt_archive

    try:
        archive = open_rt_archive(readonly=True)
    except Exception:
        archive = None

    try:
        if args.base_id and args.oracle_id:
            pairs = [(args.base_id, args.oracle_id)]
        elif args.base_id or args.oracle_id:
            parser.error("--base-id and --oracle-id must be given together")
            return
        else:
            pairs = _corpus_slice(args.limit)

        reports = [_run_pair(base_id, oracle_id, archive) for base_id, oracle_id in pairs]
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    if args.json:
        print(json.dumps(_aggregate_jsonable(reports), ensure_ascii=False, indent=2, sort_keys=False))
        return

    strong_total = sum(r.strong_count for r in reports)
    triage_total = sum(r.triage_count for r in reports)
    print(f"\nEE consolidation-error candidate surface — {len(reports)} pair(s)")
    print(f"  STRONG total: {strong_total}   TRIAGE total: {triage_total}")
    for report in reports:
        _print_report(report)


if __name__ == "__main__":
    main()

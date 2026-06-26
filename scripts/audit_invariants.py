#!/usr/bin/env python3
"""Corpus-wide replay/product invariant audit for LawVM Finland statutes.

Replays every statute in the corpus and collects structural invariant findings,
plus any direct invariant lists surfaced through replay metadata.

Usage:
    uv run python scripts/audit_invariants.py
    uv run python scripts/audit_invariants.py --sample-size 50
    uv run python scripts/audit_invariants.py --workers 4
    uv run python scripts/audit_invariants.py --corpus path/to/ids.txt
    uv run python scripts/audit_invariants.py --filter-detector-family flattened_sublist_family
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from collections import Counter
from pathlib import Path

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))

from lawvm.tools.audit_channels import invariants_channel_spec, run_audit_channel  # noqa: E402
from lawvm.tools.fi_invariant_audit import (  # noqa: E402
    _annotate_phase_scope,
    _audit_one,
    _classify_typed_tree_violation,
    _classify_violation,
    annotate_phase_scope,
    audit_one_statute,
    classify_typed_tree_violation,
    classify_violation,
    load_corpus,
)

DEFAULT_CORPUS = LAWVM_DIR / ".tmp" / "diff_triage_corpus.txt"
DEFAULT_OUTPUT = LAWVM_DIR / ".tmp" / "invariant_audit.csv"

__all__ = [
    "_annotate_phase_scope",
    "_audit_one",
    "_classify_typed_tree_violation",
    "_classify_violation",
    "annotate_phase_scope",
    "audit_one_statute",
    "classify_typed_tree_violation",
    "classify_violation",
    "load_corpus",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Corpus-wide tree invariant audit for LawVM Finland statutes."
    )
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--sample-size", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--filter-phase-scope", metavar="SCOPE", default="")
    parser.add_argument("--filter-detector-family", metavar="FAMILY", default="")
    parser.add_argument("--filter-violation-type", metavar="TYPE", default="")
    parser.add_argument("--min-chain-length", type=int, default=0)
    parser.add_argument("--filter-actionability", metavar="LEVEL", default="")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args(argv)

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ids = load_corpus(corpus_path)
    if not ids:
        print("ERROR: corpus is empty", file=sys.stderr)
        return 1

    if args.sample_size and args.sample_size < len(ids):
        rng = random.Random(args.seed)
        ids = rng.sample(ids, args.sample_size)
        print(f"Sampling {len(ids)} statutes (seed={args.seed})")
    else:
        print(f"Processing all {len(ids)} statutes from corpus")

    workers = max(1, args.workers)
    print(f"Workers: {workers}")
    print(f"Output: {output_path}")
    print()

    all_rows: list[dict[str, str]] = []
    error_count = 0
    violation_count = 0
    processed = 0
    start = time.monotonic()

    fieldnames = [
        "statute_id",
        "audit_status",
        "violation_type",
        "path",
        "detail",
        "source",
        "adj_kind",
        "phase",
        "surface",
        "profile_id",
        "replay_profile_id",
        "chain_length",
        "oracle_suspect",
        "inferred_phase",
        "phase_scope",
        "detector_family",
        "actionability",
    ]

    def on_progress(done: int, total: int, _sid: str) -> None:
        if done % 50 == 0 or done == total:
            elapsed = time.monotonic() - start
            rate = done / elapsed if elapsed > 0 else 0
            print(f"  {done}/{total} processed  {rate:.1f} stat/s")

    sweep = run_audit_channel(
        invariants_channel_spec(),
        ids,
        workers=workers,
        on_progress=on_progress,
    )
    for _sid, rows in sweep.rows:
        processed += 1
        for row in rows:
            if row["violation_type"] == "ERROR":
                error_count += 1
            else:
                violation_count += 1
        all_rows.extend(rows)

    all_rows = annotate_phase_scope(all_rows)

    elapsed = time.monotonic() - start
    print(f"\nDone in {elapsed:.1f}s — {processed} statutes")

    if not args.summary_only:
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"Wrote {len(all_rows)} rows to {output_path}")

    violation_rows = [row for row in all_rows if row["violation_type"] != "ERROR"]
    summary_rows = violation_rows
    filter_active = False
    filter_desc_parts: list[str] = []

    if args.filter_phase_scope:
        summary_rows = [row for row in summary_rows if row.get("phase_scope") == args.filter_phase_scope]
        filter_desc_parts.append(f"phase_scope={args.filter_phase_scope!r}")
        filter_active = True
    if args.filter_detector_family:
        summary_rows = [
            row for row in summary_rows if row.get("detector_family") == args.filter_detector_family
        ]
        filter_desc_parts.append(f"detector_family={args.filter_detector_family!r}")
        filter_active = True
    if args.filter_violation_type:
        summary_rows = [
            row for row in summary_rows if row.get("violation_type") == args.filter_violation_type
        ]
        filter_desc_parts.append(f"violation_type={args.filter_violation_type!r}")
        filter_active = True
    if args.min_chain_length:
        summary_rows = [
            row
            for row in summary_rows
            if row.get("chain_length", "").strip().lstrip("-").isdigit()
            and int(row["chain_length"]) >= args.min_chain_length
        ]
        filter_desc_parts.append(f"chain_length>={args.min_chain_length}")
        filter_active = True
    if args.filter_actionability:
        summary_rows = [
            row for row in summary_rows if row.get("actionability") == args.filter_actionability
        ]
        filter_desc_parts.append(f"actionability={args.filter_actionability!r}")
        filter_active = True

    print("\n=== Summary ===")
    if filter_active:
        print(f"Active filters: {', '.join(filter_desc_parts)}")
        print(f"Rows after filter: {len(summary_rows)} of {len(violation_rows)} violation rows")
    print(f"Total statutes processed : {processed}")
    print(f"Statutes with violations : {len({row['statute_id'] for row in violation_rows})}")
    print(f"Total violations         : {len(violation_rows)}")
    print(f"Compile errors           : {error_count}")

    if summary_rows:
        type_counts: Counter[str] = Counter(row["violation_type"] for row in summary_rows)
        print("\nTop violation types:")
        for vtype, count in type_counts.most_common(10):
            print(f"  {vtype:35s}  {count:6d}")

        scope_counts: Counter[str] = Counter(row["phase_scope"] for row in summary_rows)
        print("\nPhase scopes:")
        for scope, count in scope_counts.most_common(10):
            print(f"  {scope:35s}  {count:6d}")

        detector_counts: Counter[str] = Counter(row["detector_family"] for row in summary_rows)
        print("\nDetector families:")
        for family, count in detector_counts.most_common(10):
            print(f"  {family:35s}  {count:6d}")

        actionability_counts: Counter[str] = Counter(
            row.get("actionability", "") for row in summary_rows
        )
        print("\nActionability:")
        for level, count in actionability_counts.most_common(10):
            print(f"  {level:35s}  {count:6d}")

        detail_counts: Counter[str] = Counter(row["detail"] for row in summary_rows)
        print("\nTop violation details (by pattern):")
        for detail, count in detail_counts.most_common(15):
            print(f"  {count:6d}  {detail[:80]}")

        if filter_active:
            print(f"\nMatching statutes ({len({row['statute_id'] for row in summary_rows})}):")
            statute_counts: Counter[str] = Counter(row["statute_id"] for row in summary_rows)
            for sid, count in statute_counts.most_common(20):
                print(f"  {sid:20s}  {count:4d} violation(s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

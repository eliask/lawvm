#!/usr/bin/env python3
"""Corpus-wide finding-ledger audit for Finland statutes.

Compiles every statute in the corpus and aggregates projected finding-ledger
rows by kind, producing a report of the most common failure modes.

Usage:
    uv run python scripts/audit_adjudications.py
    uv run python scripts/audit_adjudications.py --workers 8
    uv run python scripts/audit_adjudications.py --corpus .tmp/audit_sample.txt
    uv run python scripts/audit_adjudications.py --resume
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))

from lawvm.tools.audit_channels import adjudications_channel_spec, run_audit_channel  # noqa: E402
from lawvm.tools.fi_adjudication_audit import (  # noqa: E402
    AdjRow,
    FailureRow,
    WorkerResult,
    compile_one_statute,
    compile_one_statute as _compile_one,
)

__all__ = ["AdjRow", "FailureRow", "WorkerResult", "_compile_one", "compile_one_statute"]

DEFAULT_CORPUS = str(LAWVM_DIR / ".tmp" / "diff_triage_corpus.txt")
DEFAULT_OUTPUT = str(LAWVM_DIR / ".tmp" / "adjudication_audit.csv")


def _load_corpus(path: str) -> list[str]:
    from lawvm.tools.corpus_io import load_statute_ids, resolve_line_list_source

    return load_statute_ids(resolve_line_list_source(Path(path)))


def _deduplicate_ids(raw_ids: list[str]) -> list[str]:
    from lawvm.tools.corpus_io import deduplicate_parent_ids

    return deduplicate_parent_ids(raw_ids)


# ---------------------------------------------------------------------------
# Resume support
# ---------------------------------------------------------------------------

def _load_already_done(output_path: str) -> set[str]:
    """Return the set of statute IDs already written to the output CSV."""
    p = Path(output_path)
    if not p.exists():
        return set()
    done: set[str] = set()
    with open(output_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            done.add(row.get("statute_id", ""))
    return done


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _print_summary(
    adj_kind_counts: Counter[str],
    statute_kind_counts: Counter[tuple[str, str]],
    failure_kind_counts: Counter[str],
    samples: dict[str, list[str]],
    statute_kind_samples: dict[tuple[str, str], list[str]],
    failure_samples: dict[str, list[str]],
    total: int,
    errors: dict[str, str],
    warning_total: int,
) -> None:
    print()
    print("=" * 60)
    print(f"ADJUDICATION AUDIT SUMMARY  ({total} statutes)")
    print("=" * 60)

    if errors:
        print(f"\n  Compile errors: {len(errors)}")
        for sid, msg in list(errors.items())[:5]:
            print(f"    {sid}: {msg[:80]}")
        if len(errors) > 5:
            print(f"    ... and {len(errors) - 5} more")

    print(f"\n  Total warnings captured: {warning_total}")

    print(f"\n  Finding kind distribution ({sum(adj_kind_counts.values())} total):")
    for kind, count in adj_kind_counts.most_common(20):
        sample_list = samples.get(kind, [])[:3]
        sample_str = ", ".join(sample_list)
        print(f"    {count:6d}  {kind}")
        if sample_str:
            print(f"            e.g. {sample_str}")

    print(
        f"\n  Statute/kind groups ({sum(statute_kind_counts.values())} total rows):"
    )
    for (statute_id, adj_kind), count in statute_kind_counts.most_common(20):
        sample_list = statute_kind_samples.get((statute_id, adj_kind), [])[:3]
        sample_str = "; ".join(sample_list)
        print(f"    {count:6d}  {statute_id:<12}  {adj_kind}")
        if sample_str:
            print(f"            e.g. {sample_str}")

    print(f"\n  Failed-op reason distribution ({sum(failure_kind_counts.values())} total):")
    for kind, count in failure_kind_counts.most_common(20):
        sample_list = failure_samples.get(kind, [])[:3]
        sample_str = ", ".join(sample_list)
        print(f"    {count:6d}  {kind}")
        if sample_str:
            print(f"            e.g. {sample_str}")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Corpus-wide adjudication audit")
    parser.add_argument(
        "--corpus",
        default=DEFAULT_CORPUS,
        help="Path to corpus file (one ID per line)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Path to output CSV file",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers (default 4)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip statute IDs already present in the output CSV",
    )
    args = parser.parse_args()

    corpus_path = args.corpus
    output_path = args.output
    workers = max(1, args.workers)

    # Load and deduplicate corpus
    raw_ids = _load_corpus(corpus_path)
    ids = _deduplicate_ids(raw_ids)
    print(f"Corpus: {len(raw_ids)} raw entries → {len(ids)} unique parent IDs")

    # Resume support
    already_done: set[str] = set()
    if args.resume:
        already_done = _load_already_done(output_path)
        pending = [sid for sid in ids if sid not in already_done]
        print(f"Resume: {len(already_done)} already done, {len(pending)} to process")
    else:
        pending = ids

    if not pending:
        print("Nothing to compile — all done.")
        return

    # Prepare output file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.resume or not output_file.exists()

    csv_columns = ["statute_id", "adj_kind", "message", "source_statute"]

    # Accumulators for summary
    adj_kind_counts: Counter[str] = Counter()
    statute_kind_counts: Counter[tuple[str, str]] = Counter()
    failure_kind_counts: Counter[str] = Counter()
    adj_samples: dict[str, list[str]] = defaultdict(list)
    statute_kind_samples: dict[tuple[str, str], list[str]] = defaultdict(list)
    failure_samples: dict[str, list[str]] = defaultdict(list)
    errors: dict[str, str] = {}
    warning_total = 0
    processed = 0
    t0 = time.monotonic()

    with open(output_path, "a" if args.resume else "w", newline="") as csv_out:
        writer = csv.DictWriter(csv_out, fieldnames=csv_columns)
        if write_header:
            writer.writeheader()

        def on_progress(done: int, total: int, _sid: str) -> None:
            if done % 50 == 0 or done == total:
                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate > 0 else 0
                print(
                    f"  {done}/{total}  "
                    f"{elapsed:.0f}s elapsed  "
                    f"{rate:.1f} sid/s  "
                    f"ETA {eta:.0f}s",
                    end="\r",
                    flush=True,
                )

        sweep = run_audit_channel(
            adjudications_channel_spec(),
            pending,
            workers=workers,
            on_progress=on_progress,
        )
        for sid, res in sweep.rows:
            if not isinstance(res, WorkerResult):
                errors[sid] = "invalid worker result"
                processed += 1
                continue
            if res.error:
                errors[sid] = res.error

            for row in res.adj_rows:
                writer.writerow({
                    "statute_id": row.statute_id,
                    "adj_kind": row.adj_kind,
                    "message": row.message,
                    "source_statute": row.source_statute,
                })
                adj_kind_counts[row.adj_kind] += 1
                statute_kind_key = (row.statute_id, row.adj_kind)
                statute_kind_counts[statute_kind_key] += 1
                if len(adj_samples[row.adj_kind]) < 5:
                    adj_samples[row.adj_kind].append(sid)
                if len(statute_kind_samples[statute_kind_key]) < 5:
                    statute_kind_samples[statute_kind_key].append(
                        f"{row.message} [{row.source_statute}]"
                    )

            for frow in res.failure_rows:
                failure_kind_counts[frow.failure_kind] += 1
                if len(failure_samples[frow.failure_kind]) < 5:
                    failure_samples[frow.failure_kind].append(sid)

            warning_total += res.warning_count
            processed += 1

    print()  # newline after progress line
    total_compiled = len(ids) if args.resume else len(pending)
    _print_summary(
        adj_kind_counts=adj_kind_counts,
        statute_kind_counts=statute_kind_counts,
        failure_kind_counts=failure_kind_counts,
        samples=adj_samples,
        statute_kind_samples=statute_kind_samples,
        failure_samples=failure_samples,
        total=total_compiled,
        errors=errors,
        warning_total=warning_total,
    )

    print(f"CSV written to: {output_path}")


if __name__ == "__main__":
    main()

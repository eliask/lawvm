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
import warnings
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))

DEFAULT_CORPUS = str(LAWVM_DIR / ".tmp" / "diff_triage_corpus.txt")
DEFAULT_OUTPUT = str(LAWVM_DIR / ".tmp" / "adjudication_audit.csv")


# ---------------------------------------------------------------------------
# Worker (runs in subprocess via ProcessPoolExecutor)
# ---------------------------------------------------------------------------

class AdjRow(NamedTuple):
    statute_id: str
    adj_kind: str
    message: str
    source_statute: str


class FailureRow(NamedTuple):
    statute_id: str
    failure_kind: str
    description: str
    source_statute: str


class WorkerResult(NamedTuple):
    sid: str
    adj_rows: list[AdjRow]
    failure_rows: list[FailureRow]
    warning_count: int
    error: str  # empty string means success


def _compile_one(sid: str) -> WorkerResult:
    """Compile one statute; return projected findings, blocking rows, warning count."""
    try:
        from lawvm.core.compile_views import projection_rows_from_findings  # noqa: PLC0415
        from lawvm.finland.compile import compile_fi_facade  # noqa: PLC0415

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            facade = compile_fi_facade(sid, replay_mode="legal_pit", compile_mode="quirks")
        projection_rows = projection_rows_from_findings(facade.finding_ledger)

        adj_rows: list[AdjRow] = [
            AdjRow(
                statute_id=sid,
                adj_kind=str(row.get("kind") or ""),
                message=str(row.get("message") or ""),
                source_statute=str(row.get("source") or ""),
            )
            for row in projection_rows
        ]
        failure_rows: list[FailureRow] = [
            FailureRow(
                statute_id=sid,
                failure_kind=str(row.get("kind") or ""),
                description=str(row.get("message") or ""),
                source_statute=str(row.get("source") or ""),
            )
            for row in projection_rows
            if bool(row.get("blocking"))
            or str(row.get("role") or "") in {"obligation", "violation"}
        ]
        return WorkerResult(
            sid=sid,
            adj_rows=adj_rows,
            failure_rows=failure_rows,
            warning_count=len(caught),
            error="",
        )
    except Exception as exc:  # noqa: BLE001
        return WorkerResult(
            sid=sid,
            adj_rows=[],
            failure_rows=[],
            warning_count=0,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

def _load_corpus(path: str) -> list[str]:
    """Load statute IDs from corpus file. Each line is like '1896/37-000'."""
    ids: list[str] = []
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
    return ids


def _normalize_id(raw_id: str) -> str:
    """Strip -NNN amendment suffix so '1896/37-000' becomes '1896/37'."""
    # The corpus contains lines like '1896/37-000' where '-000' is the
    # amendment index.  compile_fi_facade wants just the parent id.
    if "-" in raw_id:
        # Only strip numeric suffixes, not hyphens inside the year/number
        # corpus format: YEAR/NUMBER-AMENDIDX  e.g. 1896/37-000
        parts = raw_id.rsplit("-", 1)
        if parts[1].isdigit():
            return parts[0]
    return raw_id


def _deduplicate_ids(raw_ids: list[str]) -> list[str]:
    """Deduplicate by normalized parent id, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for rid in raw_ids:
        nid = _normalize_id(rid)
        if nid not in seen:
            seen.add(nid)
            result.append(nid)
    return result


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

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_compile_one, sid): sid for sid in pending}
            total_pending = len(futures)

            for fut in as_completed(futures):
                sid = futures[fut]
                try:
                    res: WorkerResult = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors[sid] = f"future error: {exc}"
                    processed += 1
                    continue

                if res.error:
                    errors[sid] = res.error

                # Write projected finding rows
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

                # Accumulate failure stats (not written to adj CSV)
                for frow in res.failure_rows:
                    failure_kind_counts[frow.failure_kind] += 1
                    if len(failure_samples[frow.failure_kind]) < 5:
                        failure_samples[frow.failure_kind].append(sid)

                warning_total += res.warning_count
                processed += 1

                # Progress indicator every 50 statutes
                if processed % 50 == 0 or processed == total_pending:
                    elapsed = time.monotonic() - t0
                    rate = processed / elapsed if elapsed > 0 else 0
                    eta = (total_pending - processed) / rate if rate > 0 else 0
                    print(
                        f"  {processed}/{total_pending}  "
                        f"{elapsed:.0f}s elapsed  "
                        f"{rate:.1f} sid/s  "
                        f"ETA {eta:.0f}s",
                        end="\r",
                        flush=True,
                    )

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

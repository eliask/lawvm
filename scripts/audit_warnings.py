#!/usr/bin/env python3
"""Corpus-wide replay warning audit.

Compiles every statute in the corpus while capturing all Python warnings
emitted during replay, then reports the most common warning patterns.

Usage:
    uv run python scripts/audit_warnings.py
    uv run python scripts/audit_warnings.py --limit 20
    uv run python scripts/audit_warnings.py --workers 8
    uv run python scripts/audit_warnings.py --corpus .tmp/my_corpus.txt
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))

DEFAULT_CORPUS = LAWVM_DIR / ".tmp" / "diff_triage_corpus.txt"
DEFAULT_OUTPUT = LAWVM_DIR / ".tmp" / "warning_audit.csv"


def _load_corpus(path: Path) -> list[str]:
    from lawvm.tools.corpus_io import load_statute_ids, resolve_line_list_source

    return load_statute_ids(resolve_line_list_source(path))


def _deduplicate_ids(raw_ids: list[str]) -> list[str]:
    from lawvm.tools.corpus_io import deduplicate_parent_ids

    return deduplicate_parent_ids(raw_ids)


def main() -> None:
    from lawvm.tools.audit_channels import (
        normalize_warning_message,
        run_audit_channel,
        warnings_channel_spec,
    )
    from lawvm.tools.corpus_io import deduplicate_parent_ids, load_statute_ids, resolve_line_list_source

    parser = argparse.ArgumentParser(description="Corpus-wide replay warning audit")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
        sys.exit(1)

    raw_ids = load_statute_ids(resolve_line_list_source(corpus_path))
    sids = deduplicate_parent_ids(raw_ids)
    if args.limit:
        sids = sids[: args.limit]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(sids)
    print(f"Corpus   : {len(raw_ids)} raw entries -> {total} unique parent IDs")
    print(f"Workers  : {args.workers}")
    print(f"Output   : {output_path}")
    print()

    all_rows: list[dict[str, object]] = []
    error_count = 0
    warning_count = 0

    def on_progress(done: int, total_count: int, _sid: str) -> None:
        if done % 100 == 0 or done == total_count:
            print(f"  [{done:5d}/{total_count}] processed")

    sweep = run_audit_channel(
        warnings_channel_spec(),
        sids,
        workers=args.workers,
        on_progress=on_progress,
    )
    for sid, w_list in sweep.rows:
        for warning in w_list:
            if warning["category"] == "ERROR":
                error_count += 1
            else:
                warning_count += 1
            all_rows.append({"statute_id": sid, **warning})

    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["statute_id", "category", "message", "filename", "lineno"],
        )
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    print(f"\nWrote {len(all_rows)} rows to {output_path}")

    pattern_counter: Counter[tuple[str, str, str]] = Counter()
    for row in all_rows:
        if row["category"] == "ERROR":
            continue
        key = (
            str(row["category"]),
            normalize_warning_message(str(row["message"])),
            f"{row['filename']}:{row['lineno']}",
        )
        pattern_counter[key] += 1

    print(f"\n{'=' * 72}")
    print("TOP WARNING PATTERNS (by frequency)")
    print(f"{'=' * 72}")
    print(f"{'Count':>6}  {'Category':<30}  Source")
    print(f"{'':->6}  {'':->30}  {'':->30}")

    top = pattern_counter.most_common(30)
    for (category, norm_msg, source), count in top:
        short_msg = norm_msg[:80] + ("…" if len(norm_msg) > 80 else "")
        print(f"{count:6d}  {category:<30}  {source}")
        print(f"         {short_msg}")
        print()

    if not top:
        print("  No warnings collected.")

    print(f"\nTotal warnings : {warning_count}")
    print(f"Total errors   : {error_count}")
    print(f"Statutes run   : {total}")


if __name__ == "__main__":
    main()

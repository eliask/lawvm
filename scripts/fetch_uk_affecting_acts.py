#!/usr/bin/env python3
"""Pre-fetch missing affecting act XMLs into the UK FetchArchive.

For each statute in the bench corpus (or a specific statute / set of types),
loads its effects feed from the archive, identifies structural effects whose
affecting act XML is not yet cached, and fetches them from legislation.gov.uk.

Idempotent — already-cached acts are skipped.  Rate-limited at max 2 req/sec.

Usage (from LawVM/):
    uv run python scripts/fetch_uk_affecting_acts.py --types asc --delay 0.5
    uv run python scripts/fetch_uk_affecting_acts.py --types asp --delay 0.5
    uv run python scripts/fetch_uk_affecting_acts.py --statute ukpga/1998/42
    uv run python scripts/fetch_uk_affecting_acts.py --statute ukpga/1998/42 --dry-run
    uv run python scripts/fetch_uk_affecting_acts.py  # all statutes in bench corpus
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent  # LawVM/
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_CORPUS_CSV = _REPO_ROOT / "data" / "uk" / "bench_corpus.csv"

# Minimum delay between HTTP requests.
_MIN_DELAY = 0.5


# ---------------------------------------------------------------------------
# Corpus helpers
# ---------------------------------------------------------------------------


def _load_corpus(types: frozenset[str] | None) -> list[str]:
    """Return statute IDs from the bench corpus CSV, filtered by type."""
    if not _CORPUS_CSV.exists():
        print(f"Corpus CSV not found: {_CORPUS_CSV}", file=sys.stderr)
        print("Run: lawvm bench -j uk --corpus-csv", file=sys.stderr)
        sys.exit(1)

    sids = []
    with open(_CORPUS_CSV) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if types is None or row["type"] in types:
                sids.append(row["statute_id"])
    return sids


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-fetch missing affecting act XMLs into the UK FetchArchive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--statute", metavar="SID",
        help="Fetch for a single statute (e.g. ukpga/1998/42)",
    )
    group.add_argument(
        "--types", nargs="+", metavar="TYPE",
        help="Act types to process from bench corpus (e.g. asc asp ukpga)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.8,
        help=f"Seconds between HTTP requests (min {_MIN_DELAY}, default 0.8)",
    )
    parser.add_argument(
        "--db", metavar="PATH",
        help=f"FetchArchive DB path (default: {_DEFAULT_DB})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fetched without actually downloading",
    )
    parser.add_argument(
        "--include-enacted-affecting",
        action="store_true",
        help="Also fetch /enacted/data.xml for cached or newly fetched affecting acts",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show per-act status (cached / fetched)",
    )
    parser.add_argument(
        "--events-jsonl", metavar="PATH",
        help="Write structured acquisition event rows for failed or known-missing affecting acts",
    )
    args = parser.parse_args()

    delay = max(args.delay, _MIN_DELAY)
    db_path = Path(args.db) if args.db else _DEFAULT_DB
    events_path = Path(args.events_jsonl) if args.events_jsonl else None

    if not db_path.exists():
        print(f"Archive DB not found: {db_path}", file=sys.stderr)
        print("Run: uv run lawvm uk-corpus all", file=sys.stderr)
        sys.exit(1)

    from farchive import Farchive
    from lawvm.uk_legislation.uk_prefetch import fetch_missing_for_statute

    archive = Farchive(db_path)

    # Build list of statute IDs to process
    if args.statute:
        sids = [args.statute]
        print(f"Processing single statute: {args.statute}")
    elif args.types:
        sids = _load_corpus(frozenset(args.types))
        print(f"Processing {len(sids)} statutes (types: {sorted(args.types)})")
    else:
        sids = _load_corpus(None)
        print(f"Processing all {len(sids)} statutes in bench corpus")

    if not sids:
        print("No statutes to process.", file=sys.stderr)
        archive.close()
        sys.exit(0)

    if args.dry_run:
        print("DRY-RUN mode — nothing will be downloaded.")
    if events_path:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text("", encoding="utf-8")

    total_fetched = total_cached = total_errors = 0
    total_events = 0
    event_rule_counts: Counter[str] = Counter()
    blocking_event_rule_counts: Counter[str] = Counter()
    n_statute = len(sids)

    for i, sid in enumerate(sids):
        report = fetch_missing_for_statute(
            sid,
            archive,
            delay=delay,
            dry_run=args.dry_run,
            verbose=args.verbose,
            include_enacted=args.include_enacted_affecting,
        )
        fetched, cached, errors = report
        total_fetched += fetched
        total_cached += cached
        total_errors += errors
        events = list(getattr(report, "events", ()) or ())
        total_events += len(events)
        for event in events:
            rule_id = str(event.get("rule_id") or "unknown")
            event_rule_counts[rule_id] += 1
            if bool(event.get("blocking", True)):
                blocking_event_rule_counts[rule_id] += 1
        if events_path and events:
            with events_path.open("a", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")

        # Progress every 10 statutes
        if args.verbose or (i + 1) % 10 == 0 or (i + 1) == n_statute:
            print(
                f"[{i+1}/{n_statute}] {sid}  "
                f"fetched={fetched} cached={cached} errors={errors}  "
                f"(total: fetched={total_fetched} cached={total_cached} errors={total_errors})"
            )

    print()
    print("=== Done ===")
    print(f"Statutes processed:  {n_statute}")
    print(f"Acts fetched:        {total_fetched}")
    print(f"Acts already cached: {total_cached}")
    print(f"Fetch errors:        {total_errors}")
    if total_events:
        print(
            "Acquisition event rules: "
            + ", ".join(f"{rule}={count}" for rule, count in sorted(event_rule_counts.items()))
        )
    if blocking_event_rule_counts:
        print(
            "Blocking event rules:    "
            + ", ".join(
                f"{rule}={count}" for rule, count in sorted(blocking_event_rule_counts.items())
            )
        )
    if events_path:
        print(f"Acquisition events:  {total_events}")
        print(f"Events JSONL:        {events_path}")

    archive.close()

    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

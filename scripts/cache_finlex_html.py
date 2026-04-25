#!/usr/bin/env python3
"""Bulk-fetch all Finlex consolidated HTML pages into FetchArchive cache.

Usage (from LawVM/ dir):
    nice -n 19 uv run python scripts/cache_finlex_html.py [--workers 3] [--delay 1.0]

Fetches HTML for every statute in the corpus. Skips already-cached entries
(checks FetchArchive for existing observations). Rate-limited per worker.

At 3 workers × 1s delay ≈ 3 req/s → ~5.5h for 59K statutes.
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lawvm.fetch_archive import FetchArchive  # ty: ignore[unresolved-import]  # legacy: module deleted
from lawvm.finland.finlex_html import (
    _DEFAULT_CACHE,
    _curl_fetch,
    _finlex_html_url,
    _html_locator,
)
from lawvm.finland.grafter import get_corpus

# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

_print_lock = Lock()
_stats: dict[str, float | int] = {"done": 0, "cached": 0, "fetched": 0, "failed": 0, "total": 0, "start": 0.0}


def _progress(sid: str, status: str) -> None:
    with _print_lock:
        _stats["done"] += 1
        _stats[status] += 1
        done = _stats["done"]
        total = _stats["total"]
        if done % 100 == 0 or done == total:
            elapsed = time.monotonic() - _stats["start"]
            rate = done / elapsed if elapsed > 0 else 0
            eta_s = (total - done) / rate if rate > 0 else 0
            eta_h = eta_s / 3600
            print(
                f"[{done:>6}/{total}] {status:>7} {sid:<16} "
                f"({_stats['fetched']} fetched, {_stats['cached']} cached, "
                f"{_stats['failed']} failed, {rate:.1f}/s, ETA {eta_h:.1f}h)"
            )


# ---------------------------------------------------------------------------
# Per-statute fetch
# ---------------------------------------------------------------------------


def _fetch_one(sid: str, db_path: Path, max_age_hours: float, delay: float) -> str:
    """Fetch one statute's HTML, return status string.

    Each call creates its own FetchArchive instance (own SQLite connection +
    own fcntl lock fd) to avoid thread-safety issues with shared connections.
    """
    parts = sid.split("/")
    if len(parts) != 2:
        return "failed"

    year, num = parts
    # Strip trailing suffixes like "-000", "-001"
    num = num.split("-")[0]

    locator = _html_locator(year, num)
    archive = FetchArchive(db_path)

    # Skip if fresh enough
    if archive.is_fresh(locator, max_age_hours):
        return "cached"

    # Rate limit per call
    time.sleep(delay)

    url = _finlex_html_url(year, num)
    html = _curl_fetch(url)
    if html is None:
        return "failed"

    archive.store(locator, html, content_type="html")
    return "fetched"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-cache Finlex HTML pages")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent fetch workers (default: 3)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests per worker (default: 1.0)")
    parser.add_argument("--max-age-hours", type=float, default=720.0, help="Cache TTL in hours (default: 720 = 30 days)")
    parser.add_argument("--min-year", type=int, default=0, help="Only fetch statutes from this year onwards")
    parser.add_argument("--corpus", type=str, default="", help="CSV file with statute IDs (col 1=count, col 2=sid)")
    args = parser.parse_args()

    if args.corpus:
        # Read SIDs from bench corpus CSV (format: count,sid)
        all_sids = []
        with open(args.corpus) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                if len(parts) >= 2:
                    all_sids.append(parts[1].strip())
        all_sids = sorted(set(all_sids))
    else:
        corpus = get_corpus()
        all_sids = sorted(corpus.list_statute_ids())

    if args.min_year:
        all_sids = [s for s in all_sids if int(s.split("/")[0]) >= args.min_year]

    print(f"Statutes: {len(all_sids)}, workers: {args.workers}, delay: {args.delay}s")
    print(f"Cache DB: {_DEFAULT_CACHE}")
    print(f"Max age: {args.max_age_hours}h ({args.max_age_hours/24:.0f} days)")
    print()

    db_path = _DEFAULT_CACHE
    _stats["total"] = len(all_sids)
    _stats["start"] = time.monotonic()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_fetch_one, sid, db_path, args.max_age_hours, args.delay): sid
            for sid in all_sids
        }
        for fut in as_completed(futures):
            sid = futures[fut]
            try:
                status = fut.result()
            except Exception as e:
                status = "failed"
                with _print_lock:
                    print(f"  ERROR {sid}: {e}", file=sys.stderr)
            _progress(sid, status)

    elapsed = time.monotonic() - _stats["start"]
    print(f"\nDone in {elapsed/3600:.1f}h. "
          f"Fetched: {_stats['fetched']}, Cached: {_stats['cached']}, Failed: {_stats['failed']}")


if __name__ == "__main__":
    main()

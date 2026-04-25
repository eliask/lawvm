"""acquire_ee_corpus.py — Acquire full Estonian Riigi Teataja corpus.

Discovery via RT's publication feed hierarchy:
  1. Master feed: ilmunud_ilmumas.xml → lists all publication days since 1996
  2. Daily feeds: ilmumised_tulemus.xml?kpv=DD.MM.YYYY&rtOsaId=N → per-act links
  3. Act XMLs:    akt/AKTVIIDE.xml → full statute/amendment XML

All I/O goes through FetchArchive (SQLite+zstd, content-addressed, resumable).

RT parts:
  rtOsaId=2: RT I  (seadused, VV määrused — laws + government decrees)
  rtOsaId=3: RT II (ministrite määrused — ministerial decrees)
  rtOsaId=4: RT III (KOV — local government)
  rtOsaId=5: RT IV  (EU)

Usage (from LawVM/ dir):
    uv run python scripts/acquire_ee_corpus.py                    # full pipeline
    uv run python scripts/acquire_ee_corpus.py --phase 1          # discover daily feeds only
    uv run python scripts/acquire_ee_corpus.py --phase 2          # fetch act XMLs (needs phase 1)
    uv run python scripts/acquire_ee_corpus.py --parts 2          # RT I only (laws)
    uv run python scripts/acquire_ee_corpus.py --parts 2,3        # RT I + RT II
    uv run python scripts/acquire_ee_corpus.py --workers 8        # parallel XML fetches
    uv run python scripts/acquire_ee_corpus.py --delay 0.5        # polite delay between fetches
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lawvm.fetch_archive import FetchArchive  # ty: ignore[unresolved-import]  # legacy: module deleted, script needs farchive migration

_BASE_URL = "https://www.riigiteataja.ee"
_MASTER_FEED = f"{_BASE_URL}/ilmunud_ilmumas.xml"
_DAILY_FEED = f"{_BASE_URL}/ilmumised_tulemus.xml"
_DEFAULT_WORKERS = 4
_DEFAULT_DELAY = 0.8
_DEFAULT_PARTS = "2,3"  # RT I + RT II by default (laws + ministerial decrees)

_RT_PART_NAMES = {
    "2": "RT I (laws + VV decrees)",
    "3": "RT II (ministerial decrees)",
    "4": "RT III (local government)",
    "5": "RT IV (EU)",
}


# ---------------------------------------------------------------------------
# Phase 1: Discover all aktViide from publication feeds
# ---------------------------------------------------------------------------

def phase1_discover(db_path: Path, parts: list[str], delay: float) -> dict[str, list[str]]:
    """Discover all aktViide by crawling the publication feed hierarchy.

    Returns {rtOsaId: [aktViide, ...]} for requested parts.
    """
    archive = FetchArchive(db_path)

    # Step 1: Fetch master feed (single request, ~3MB, lists all publication days)
    print("\n=== Phase 1: Discover acts from publication feeds ===")
    print(f"  Parts: {', '.join(_RT_PART_NAMES.get(p, f'rtOsaId={p}') for p in parts)}")

    master_xml = archive.get_latest(_MASTER_FEED)
    if master_xml is None or len(master_xml) < 1000:
        print("  Fetching master feed...", end=" ", flush=True)
        master_xml = archive.fetch(_MASTER_FEED, max_age_hours=24)
        print(f"{len(master_xml):,} bytes" if master_xml else "FAILED")

    if not master_xml:
        print("  ERROR: Could not fetch master feed", file=sys.stderr)
        archive.close()
        return {}

    master_text = master_xml.decode("utf-8", errors="replace")

    # Step 2: Extract all daily feed URLs for requested parts
    daily_feeds: list[tuple[str, str]] = []  # (date_str, rtOsaId)
    for m in re.finditer(
        r'ilmumised_tulemus\.html\?kpv=([^&]+)&amp;rtOsaId=(\d+)', master_text
    ):
        date_str, osa_id = m.group(1), m.group(2)
        if osa_id in parts:
            daily_feeds.append((date_str, osa_id))

    print(f"  Daily feeds to process: {len(daily_feeds)}")

    # Step 3: Fetch each daily feed and extract aktViide
    acts_by_part: dict[str, list[str]] = {p: [] for p in parts}
    cached = 0
    fetched = 0

    for i, (date_str, osa_id) in enumerate(daily_feeds):
        feed_url = f"{_DAILY_FEED}?kpv={date_str}&rtOsaId={osa_id}"

        feed_xml = archive.get_latest(feed_url)
        if feed_xml is None:
            feed_xml = archive.fetch(feed_url, max_age_hours=float("inf"))
            fetched += 1
            if delay and fetched % 10 == 0:
                time.sleep(delay)
        else:
            cached += 1

        if feed_xml:
            text = feed_xml.decode("utf-8", errors="replace")
            # Extract aktViide from <link>https://www.riigiteataja.ee/akt/AKTVIIDE</link>
            for aid in re.findall(r'riigiteataja\.ee/akt/(\d+)', text):
                acts_by_part[osa_id].append(aid)

        if (i + 1) % 500 == 0:
            total_acts = sum(len(v) for v in acts_by_part.values())
            print(f"  {i+1}/{len(daily_feeds)} feeds ({cached} cached, {fetched} fetched, "
                  f"{total_acts:,} acts found)...", file=sys.stderr)

    archive.close()

    total = sum(len(v) for v in acts_by_part.values())
    # Deduplicate
    for p in acts_by_part:
        acts_by_part[p] = sorted(set(acts_by_part[p]))
    total_dedup = sum(len(v) for v in acts_by_part.values())

    print(f"\n  Phase 1 complete: {total:,} act references ({total_dedup:,} unique)")
    for p in parts:
        print(f"    {_RT_PART_NAMES.get(p, f'rtOsaId={p}')}: {len(acts_by_part[p]):,} unique acts")

    return acts_by_part


# ---------------------------------------------------------------------------
# Phase 2: Fetch act XMLs
# ---------------------------------------------------------------------------

def phase2_fetch(
    db_path: Path,
    acts: list[str],
    workers: int,
    delay: float,
) -> None:
    """Fetch act XMLs for all discovered aktViide.

    Strategy: single-threaded cache check (fast, no contention), then
    parallel fetch of uncached acts only (urllib, one connection per thread).
    """
    print(f"\n=== Phase 2: Fetch {len(acts):,} act XMLs ===")
    print(f"    workers={workers}  delay={delay}s  db={db_path}")

    # Step 1: Single-threaded cache scan — check which acts are already fetched
    archive = FetchArchive(db_path)
    uncached: list[str] = []
    cached = 0
    for aid in acts:
        url = f"{_BASE_URL}/akt/{aid}.xml"
        if archive.get_latest(url) is not None:
            cached += 1
        else:
            uncached.append(aid)
    archive.close()

    print(f"  Already cached: {cached:,}, need to fetch: {len(uncached):,}")
    if not uncached:
        print("  Nothing to fetch.")
        return

    # Step 2: Parallel fetch of uncached acts
    # Each thread gets its own long-lived archive connection via thread-local storage
    _local = threading.local()
    _lock = threading.Lock()
    _stats = {"fetched": 0, "failed": 0}

    def _fetch_one(aid: str) -> tuple[str, bool]:
        if not hasattr(_local, "archive"):
            _local.archive = FetchArchive(db_path)

        url = f"{_BASE_URL}/akt/{aid}.xml"
        try:
            data = _local.archive.fetch(url, max_age_hours=float("inf"))
            if data and len(data) > 100:
                with _lock:
                    _stats["fetched"] += 1
                if delay:
                    time.sleep(delay)
                return aid, True
            else:
                with _lock:
                    _stats["failed"] += 1
                return aid, False
        except Exception:
            with _lock:
                _stats["failed"] += 1
            return aid, False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, aid): aid for aid in uncached}
        for i, future in enumerate(as_completed(futures)):
            aid, ok = future.result()
            if (i + 1) % 200 == 0:
                with _lock:
                    f, e = _stats["fetched"], _stats["failed"]
                print(f"  {i+1}/{len(uncached)}: {f} fetched, {e} failed",
                      file=sys.stderr)

    print(f"\n  Phase 2 complete: {cached:,} cached, {_stats['fetched']:,} fetched, "
          f"{_stats['failed']:,} failed")

    # Print archive stats
    archive = FetchArchive(db_path)
    stats = archive.stats()
    raw_mb = stats.get('total_raw_bytes', 0) / 1e6
    stored_mb = stats.get('total_stored_bytes', 0) / 1e6
    print(f"  Archive: {stats['n_urls']:,} URLs, {stats['n_blobs']:,} blobs, "
          f"{raw_mb:.1f} MB raw, {stored_mb:.1f} MB stored")
    archive.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Acquire Estonian RT corpus")
    parser.add_argument("--db", type=Path, default=Path(".tmp/riigiteataja_archive.db"),
                        help="FetchArchive DB path")
    parser.add_argument("--phase", type=int, choices=[1, 2], default=None,
                        help="Run only phase 1 (discover) or 2 (fetch). Default: both.")
    parser.add_argument("--parts", type=str, default=_DEFAULT_PARTS,
                        help="Comma-separated rtOsaId values (default: 2,3 = RT I + RT II)")
    parser.add_argument("--workers", type=int, default=_DEFAULT_WORKERS,
                        help=f"Parallel workers for phase 2 (default: {_DEFAULT_WORKERS})")
    parser.add_argument("--delay", type=float, default=_DEFAULT_DELAY,
                        help=f"Delay between fetches in seconds (default: {_DEFAULT_DELAY})")
    args = parser.parse_args()

    parts = [p.strip() for p in args.parts.split(",")]
    db_path = args.db

    if args.phase is None or args.phase == 1:
        acts_by_part = phase1_discover(db_path, parts, args.delay)
        all_acts = sorted(set(aid for aids in acts_by_part.values() for aid in aids))
    else:
        # Phase 2 only: re-discover from cached feeds
        acts_by_part = phase1_discover(db_path, parts, delay=0)
        all_acts = sorted(set(aid for aids in acts_by_part.values() for aid in aids))

    if args.phase is None or args.phase == 2:
        if not all_acts:
            print("No acts to fetch. Run phase 1 first.", file=sys.stderr)
            sys.exit(1)
        phase2_fetch(db_path, all_acts, args.workers, args.delay)


if __name__ == "__main__":
    main()

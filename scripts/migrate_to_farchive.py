#!/usr/bin/env python3
"""Migrate FetchArchive (.db) → Farchive (.farchive).

Reads all content via FetchArchive.get_content() (handles decompression),
stores via Farchive.store() (handles recompression + auto dict training).
Old DB is never modified.

Phase 3 (--ingest-html-cache): ingest .tmp/finlex_html_cache.db into the
same Farchive dest DB.  HTML entries use locators like
finlex://html/ajantasa/{year}/{num} with storage_class="html".

Usage:
    uv run python scripts/migrate_to_farchive.py [--source .tmp/finlex_archive.db] [--dest data/finlex.farchive]
    uv run python scripts/migrate_to_farchive.py --ingest-html-cache [--html-cache .tmp/finlex_html_cache.db] [--dest data/finlex.farchive]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path for lawvm imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lawvm.fetch_archive import FetchArchive  # ty: ignore[unresolved-import]  # legacy: module deleted
from farchive import Farchive, CompressionPolicy


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate FetchArchive → Farchive")
    p.add_argument("--source", default=".tmp/finlex_archive.db", help="Source FetchArchive DB")
    p.add_argument("--dest", default="data/finlex.farchive", help="Destination Farchive DB")
    p.add_argument("--batch-size", type=int, default=500, help="Commit every N blobs")
    p.add_argument("--skip-existing", action="store_true", help="Skip locators already in dest")
    p.add_argument(
        "--ingest-html-cache",
        action="store_true",
        help="Phase 3: ingest finlex_html_cache.db into the Farchive dest",
    )
    p.add_argument(
        "--html-cache",
        default=".tmp/finlex_html_cache.db",
        help="Source HTML cache FetchArchive DB for --ingest-html-cache",
    )
    return p.parse_args()


def _content_type_to_storage_class(content_type: str | None) -> str:
    """Map FetchArchive content_type to farchive storage_class."""
    if content_type in ("xml", "html", "pdf", "json", "gif"):
        return content_type
    return "unknown"


def _iso_to_datetime(iso_str: str | None) -> datetime | None:
    """Convert ISO timestamp to datetime for farchive's observed_at."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def migrate(source_path: str, dest_path: str, batch_size: int, skip_existing: bool) -> None:
    if not Path(source_path).exists():
        print(f"ERROR: source {source_path} not found")
        sys.exit(1)
    if Path(dest_path).exists():
        if not skip_existing:
            print(f"WARNING: {dest_path} already exists. Use --skip-existing to resume.")
            print("         Or delete it to start fresh.")
            sys.exit(1)
        print(f"Resuming migration into existing {dest_path}")

    src = FetchArchive(source_path)
    dst = Farchive(
        dest_path,
        compression=CompressionPolicy(
            auto_train_thresholds={"xml": 1000, "html": 500, "pdf": 16},
            dict_target_sizes={"xml": 112 * 1024, "html": 112 * 1024, "pdf": 64 * 1024},
            compression_level=9,
        ),
    )

    # Phase 1: count
    conn = sqlite3.connect(source_path)
    total_obs = conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
    total_blobs = conn.execute("SELECT COUNT(*) FROM blob").fetchone()[0]
    print(f"Source: {total_blobs} blobs, {total_obs} observations")

    # Phase 2: migrate observations (each carries a URL + content_hash)
    # We iterate observations, fetch raw content via FetchArchive API,
    # then store into Farchive with the locator and timestamp.
    rows = conn.execute(
        "SELECT o.url, o.content_hash, o.first_seen, o.last_seen, o.fetch_count, b.content_type "
        "FROM observation o "
        "LEFT JOIN blob b ON o.content_hash = b.content_hash "
        "ORDER BY o.first_seen"
    ).fetchall()
    conn.close()

    migrated = 0
    skipped = 0
    failed = 0
    t0 = time.monotonic()

    for i, (url, content_hash, first_seen, last_seen, fetch_count, content_type) in enumerate(rows):
        if skip_existing and dst.get(url) is not None:
            skipped += 1
            continue

        # Get raw (decompressed) content via FetchArchive API
        try:
            raw = src.get_content(content_hash)
        except Exception:
            raw = None
        if raw is None:
            failed += 1
            continue

        storage_class = _content_type_to_storage_class(content_type)
        observed_at = _iso_to_datetime(first_seen)

        dst.store(
            locator=url,
            data=raw,
            observed_at=observed_at,
            storage_class=storage_class,
        )
        migrated += 1

        if (i + 1) % batch_size == 0:
            elapsed = time.monotonic() - t0
            rate = migrated / elapsed if elapsed > 0 else 0
            print(f"  {i + 1}/{len(rows)}  migrated={migrated}  skipped={skipped}  failed={failed}  ({rate:.0f}/s)")

    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  migrated: {migrated}")
    print(f"  skipped:  {skipped}")
    print(f"  failed:   {failed}")

    # Show dest stats
    stats = dst.stats()
    print(f"\nDest stats: {stats}")

    dst.close()
    src.close()


def ingest_html_cache(html_cache_path: str, dest_path: str, skip_existing: bool) -> None:
    """Phase 3: ingest .tmp/finlex_html_cache.db (FetchArchive) into Farchive dest.

    HTML entries use locators like finlex://html/ajantasa/{year}/{num}
    with storage_class="html".  All other locators in the HTML cache are
    ingested with their original content_type mapped to storage_class.
    """
    if not Path(html_cache_path).exists():
        print(f"ERROR: HTML cache {html_cache_path} not found")
        sys.exit(1)

    print(f"Phase 3: ingesting HTML cache {html_cache_path} → {dest_path}")

    src = FetchArchive(html_cache_path)
    dst = Farchive(dest_path)

    conn = sqlite3.connect(html_cache_path)
    rows = conn.execute(
        "SELECT o.url, o.content_hash, o.first_seen, o.last_seen, b.content_type "
        "FROM observation o "
        "LEFT JOIN blob b ON o.content_hash = b.content_hash "
        "ORDER BY o.first_seen"
    ).fetchall()
    conn.close()

    print(f"  HTML cache: {len(rows)} observation(s)")

    ingested = 0
    skipped = 0
    failed = 0
    t0 = time.monotonic()

    for url, content_hash, first_seen, _last_seen, content_type in rows:
        if skip_existing and dst.get(url) is not None:
            skipped += 1
            continue

        try:
            raw = src.get_content(content_hash)
        except Exception:
            raw = None
        if raw is None:
            failed += 1
            continue

        storage_class = _content_type_to_storage_class(content_type)
        observed_at = _iso_to_datetime(first_seen)

        dst.store(
            locator=url,
            data=raw,
            observed_at=observed_at,
            storage_class=storage_class,
        )
        ingested += 1

        if ingested % 500 == 0:
            elapsed = time.monotonic() - t0
            rate = ingested / elapsed if elapsed > 0 else 0
            print(f"  {ingested}/{len(rows)}  ingested={ingested}  skipped={skipped}  failed={failed}  ({rate:.0f}/s)")

    elapsed = time.monotonic() - t0
    print(f"  Done in {elapsed:.1f}s: ingested={ingested}, skipped={skipped}, failed={failed}")

    dst.close()
    src.close()


if __name__ == "__main__":
    args = _parse_args()
    if args.ingest_html_cache:
        ingest_html_cache(args.html_cache, args.dest, args.skip_existing)
    else:
        migrate(args.source, args.dest, args.batch_size, args.skip_existing)

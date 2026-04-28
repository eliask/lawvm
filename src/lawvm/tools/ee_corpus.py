"""lawvm ee-corpus — Estonia corpus acquisition and curation helpers."""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from lawvm.estonia.fetch import open_rt_archive

if TYPE_CHECKING:
    import argparse


_BASE_URL = "https://www.riigiteataja.ee"
_MASTER_FEED = f"{_BASE_URL}/ilmunud_ilmumas.xml"
_DAILY_FEED = f"{_BASE_URL}/ilmumised_tulemus.xml"
_DEFAULT_WORKERS = 4
_DEFAULT_DELAY = 0.8
_DEFAULT_PARTS = "2,3"

_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _ROOT / "data" / "ee_riigiteataja.farchive"
_OUT_DIR = _ROOT / "data" / "estonia"
_OUT_CSV = _OUT_DIR / "bench_corpus.csv"
_OUT_NOTES = _OUT_DIR / "bench_corpus_notes.md"
_CURRENT_CSV = _OUT_DIR / "current_replayable_corpus.csv"
_CURRENT_NOTES = _OUT_DIR / "current_replayable_corpus_notes.md"
_REPLAYABLE_CSV = _OUT_DIR / "replayable_corpus.csv"
_REPLAYABLE_NOTES = _OUT_DIR / "replayable_corpus_notes.md"

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _http_fetch(url: str) -> bytes | None:
    """Fetch URL via urllib, return bytes or None on failure."""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError):
        return None


def _farchive_get_or_fetch(archive: Any, url: str, *, max_age_hours: float = float("inf")) -> bytes | None:
    """Return cached content if present; otherwise HTTP-fetch and store."""
    import math
    if math.isinf(max_age_hours):
        cached = archive.get(url)  # type: ignore[union-attr]
        if cached is not None:
            return cached
    else:
        if archive.has(url, max_age_hours=max_age_hours):  # type: ignore[union-attr]
            cached = archive.get(url)  # type: ignore[union-attr]
            if cached is not None:
                return cached
    data = _http_fetch(url)
    if data:
        archive.store(url, data)  # type: ignore[union-attr]
    return data


_RT_PART_NAMES = {
    "2": "RT I (laws + VV decrees)",
    "3": "RT II (ministerial decrees)",
    "4": "RT III (local government)",
    "5": "RT IV (EU)",
}

_BODY_TAGS = (b"<peatykk", b"<paragrahv")
_SCHEMA_MAP = [
    ("muutmisseadus", "muutmisseadus"),
    ("tyviseadus", "tyviseadus"),
    ("muutmismaarus", "muutmismaarus"),
    ("maarus", "maarus"),
    ("juurakt", "juurakt"),
]
_LAW_SCHEMAS = frozenset(["tyviseadus", "muutmisseadus"])
_DECREE_SCHEMAS = frozenset(["maarus", "muutmismaarus", "juurakt"])
_MIN_BODY_BYTES = 500


def phase1_discover(db_path: Path, parts: list[str], delay: float) -> dict[str, list[str]]:
    """Discover all aktViide ids by crawling RT publication feeds."""
    from farchive import Farchive
    archive = Farchive(db_path)
    print("\n=== Phase 1: Discover acts from publication feeds ===")
    print(f"  Parts: {', '.join(_RT_PART_NAMES.get(p, f'rtOsaId={p}') for p in parts)}")

    master_xml = archive.get(_MASTER_FEED)
    if master_xml is None or len(master_xml) < 1000:
        print("  Fetching master feed...", end=" ", flush=True)
        master_xml = _farchive_get_or_fetch(archive, _MASTER_FEED, max_age_hours=24)
        print(f"{len(master_xml):,} bytes" if master_xml else "FAILED")

    if not master_xml:
        print("  ERROR: Could not fetch master feed", file=sys.stderr)
        archive.close()
        return {}

    master_text = master_xml.decode("utf-8", errors="replace")
    daily_feeds: list[tuple[str, str]] = []
    for match in re.finditer(
        r"ilmumised_tulemus\.html\?kpv=([^&]+)&amp;rtOsaId=(\d+)",
        master_text,
    ):
        date_str, osa_id = match.group(1), match.group(2)
        if osa_id in parts:
            daily_feeds.append((date_str, osa_id))

    print(f"  Daily feeds to process: {len(daily_feeds)}")

    acts_by_part: dict[str, list[str]] = {part: [] for part in parts}
    cached = 0
    fetched = 0
    for idx, (date_str, osa_id) in enumerate(daily_feeds):
        feed_url = f"{_DAILY_FEED}?kpv={date_str}&rtOsaId={osa_id}"
        feed_xml = archive.get(feed_url)
        if feed_xml is None:
            feed_xml = _farchive_get_or_fetch(archive, feed_url, max_age_hours=float("inf"))
            fetched += 1
            if delay and fetched % 10 == 0:
                time.sleep(delay)
        else:
            cached += 1

        if feed_xml:
            text = feed_xml.decode("utf-8", errors="replace")
            for aid in re.findall(r"riigiteataja\.ee/akt/(\d+)", text):
                acts_by_part[osa_id].append(aid)

        if (idx + 1) % 500 == 0:
            total_acts = sum(len(values) for values in acts_by_part.values())
            print(
                f"  {idx + 1}/{len(daily_feeds)} feeds ({cached} cached, {fetched} fetched, "
                f"{total_acts:,} acts found)...",
                file=sys.stderr,
            )

    archive.close()

    total = sum(len(values) for values in acts_by_part.values())
    for part in acts_by_part:
        acts_by_part[part] = sorted(set(acts_by_part[part]))
    total_dedup = sum(len(values) for values in acts_by_part.values())

    print(f"\n  Phase 1 complete: {total:,} act references ({total_dedup:,} unique)")
    for part in parts:
        print(f"    {_RT_PART_NAMES.get(part, f'rtOsaId={part}')}: {len(acts_by_part[part]):,} unique acts")
    return acts_by_part


def phase2_fetch(db_path: Path, acts: list[str], workers: int, delay: float) -> None:
    """Fetch RT act XMLs for discovered aktViide ids."""
    from farchive import Farchive
    print(f"\n=== Phase 2: Fetch {len(acts):,} act XMLs ===")
    print(f"    workers={workers}  delay={delay}s  db={db_path}")

    archive = Farchive(db_path)
    uncached: list[str] = []
    cached = 0
    for aid in acts:
        url = f"{_BASE_URL}/akt/{aid}.xml"
        if archive.get(url) is not None:
            cached += 1
        else:
            uncached.append(aid)
    archive.close()

    print(f"  Already cached: {cached:,}, need to fetch: {len(uncached):,}")
    if not uncached:
        print("  Nothing to fetch.")
        return

    local = threading.local()
    lock = threading.Lock()
    stats = {"fetched": 0, "failed": 0}

    def _fetch_one(aid: str) -> tuple[str, bool]:
        if not hasattr(local, "archive"):
            from farchive import Farchive as _Farchive
            local.archive = _Farchive(db_path)
        url = f"{_BASE_URL}/akt/{aid}.xml"
        try:
            data = _farchive_get_or_fetch(local.archive, url, max_age_hours=float("inf"))
            if data and len(data) > 100:
                with lock:
                    stats["fetched"] += 1
                if delay:
                    time.sleep(delay)
                return aid, True
            with lock:
                stats["failed"] += 1
            return aid, False
        except Exception:
            with lock:
                stats["failed"] += 1
            return aid, False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, aid): aid for aid in uncached}
        for idx, future in enumerate(as_completed(futures)):
            future.result()
            if (idx + 1) % 200 == 0:
                with lock:
                    fetched = stats["fetched"]
                    failed = stats["failed"]
                print(f"  {idx + 1}/{len(uncached)}: {fetched} fetched, {failed} failed", file=sys.stderr)

    print(f"\n  Phase 2 complete: {cached:,} cached, {stats['fetched']:,} fetched, {stats['failed']:,} failed")
    archive = Farchive(db_path)
    archive_stats = archive.stats()
    raw_mb = archive_stats.total_raw_bytes / 1e6
    stored_mb = archive_stats.total_stored_bytes / 1e6
    print(
        f"  Archive: {archive_stats.locator_count:,} URLs, {archive_stats.blob_count:,} blobs, "
        f"{raw_mb:.1f} MB raw, {stored_mb:.1f} MB stored"
    )
    archive.close()


@dataclass
class _GroupInfo:
    grupi_id: str
    terviktekst_with_body: list = field(default_factory=list)
    n_amendments: int = 0
    schemas: set = field(default_factory=set)
    title: str = ""


def _classify_schema(ns: str) -> str:
    for fragment, name in _SCHEMA_MAP:
        if fragment in ns:
            return name
    return ""


def _safe_archive_size_stats(conn) -> tuple[int, int, float, float]:
    """Return basic archive size stats without depending on one Farchive schema."""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "locator_span" in tables:
        loc_rows = conn.execute("SELECT COUNT(DISTINCT locator) FROM locator_span").fetchone()
        n_urls = int(loc_rows[0] or 0) if loc_rows else 0
    elif "locator" in tables:
        loc_rows = conn.execute("SELECT COUNT(*) FROM locator").fetchone()
        n_urls = int(loc_rows[0] or 0) if loc_rows else 0
    else:
        n_urls = 0

    blob_rows = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(raw_size), 0), COALESCE(SUM(stored_self_size), 0) FROM blob"
    ).fetchone()
    n_blobs = int(blob_rows[0] or 0) if blob_rows else 0
    raw_mb = float(blob_rows[1] or 0) / 1e6 if blob_rows else 0.0
    stored_mb = float(blob_rows[2] or 0) / 1e6 if blob_rows else 0.0
    return n_urls, n_blobs, raw_mb, stored_mb


def _locator_table_name(conn) -> str:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "locator_span" in tables:
        return "locator_span"
    if "locator" in tables:
        return "locator"
    raise sqlite3.OperationalError("no locator table in archive")


def build_index(archive) -> dict[str, _GroupInfo]:
    """Scan RT archive and build group index for corpus curation."""
    conn = archive._conn
    locator_table = _locator_table_name(conn)
    rows = conn.execute(
        f"SELECT locator FROM {locator_table} WHERE locator LIKE '%riigiteataja.ee/akt/%.xml'"
    ).fetchall()
    print(f"Total XML observations: {len(rows)}")

    groups: dict[str, _GroupInfo] = {}
    t0 = time.time()
    no_grupi_id = 0
    for idx, (url,) in enumerate(rows):
        if (idx + 1) % 10000 == 0:
            elapsed = time.time() - t0
            print(
                f"  [{idx + 1}/{len(rows)}] groups={len(groups)} elapsed={elapsed:.1f}s",
                file=sys.stderr,
            )
        data = archive.get(url)
        if not data or len(data) < 100:
            continue
        aid = url.split("/akt/")[-1].replace(".xml", "")
        prefix = data[:20000]

        m_g = re.search(rb"<[^>]*terviktekstiGrupiID[^>]*>([^<]+)<", prefix)
        if not m_g:
            no_grupi_id += 1
            continue
        grupi_id = m_g.group(1).decode("utf-8", errors="replace").strip()

        m_t = re.search(rb"<[^>]*tekstiliik[^>]*>([^<]+)<", prefix)
        tekstiliik = m_t.group(1).decode("utf-8", errors="replace").strip() if m_t else ""

        m_ns = re.search(rb'xmlns\s*=\s*["\x27]([^"\x27]+)', prefix)
        ns = m_ns.group(1).decode("utf-8", errors="replace") if m_ns else ""
        schema = _classify_schema(ns)

        m_algus = re.search(rb"<[^>]*kehtivuseAlgus[^>]*>([^<]+)<", prefix)
        algus = m_algus.group(1).decode("utf-8", errors="replace").strip()[:10] if m_algus else "9999-99-99"

        m_title = re.search(rb"<[^>]*pealkiri[^>]*>([^<]+)<", prefix)
        title = m_title.group(1).decode("utf-8", errors="replace").strip() if m_title else ""

        group = groups.setdefault(grupi_id, _GroupInfo(grupi_id=grupi_id))
        if schema:
            group.schemas.add(schema)
        if title and not group.title:
            group.title = title

        n_amend = len(re.findall(rb"<[^>]*muutmismarge[^>]*>", data[:200000]))
        if n_amend > group.n_amendments:
            group.n_amendments = n_amend

        if tekstiliik == "terviktekst":
            has_structure = any(tag in data for tag in _BODY_TAGS)
            if has_structure and len(data) >= _MIN_BODY_BYTES:
                group.terviktekst_with_body.append((aid, len(data), algus))

    elapsed = time.time() - t0
    print(f"Indexed {len(rows)} blobs in {elapsed:.1f}s ({len(rows) / elapsed:.0f}/s)")
    print(f"  Unique grupi_ids: {len(groups)}")
    print(f"  No grupi_id (algtekst/standalone): {no_grupi_id}")
    return groups


def select_pairs(
    groups: dict[str, _GroupInfo],
    include_decrees: bool = True,
) -> tuple[list[tuple[str, str, str, int, str]], dict[str, int]]:
    """Select base/oracle pairs from indexed RT groups."""
    allowed = _LAW_SCHEMAS | (_DECREE_SCHEMAS if include_decrees else frozenset())
    pairs: list[tuple[str, str, str, int, str]] = []
    excluded: dict[str, int] = defaultdict(int)

    for gid, group in groups.items():
        if not group.schemas & allowed:
            excluded["schema_not_allowed"] += 1
            continue
        tvs = sorted(group.terviktekst_with_body, key=lambda item: item[2])
        if len(tvs) < 2:
            excluded["fewer_than_2_tervikteksts"] += 1
            continue
        base_id = tvs[-2][0]
        oracle_id = tvs[-1][0]
        schema = "unknown"
        for preferred in ("tyviseadus", "muutmisseadus", "maarus", "muutmismaarus", "juurakt"):
            if preferred in group.schemas:
                schema = preferred
                break
        pairs.append((gid, base_id, oracle_id, group.n_amendments, schema))

    pairs.sort(key=lambda item: (item[3], item[0]))
    return pairs, excluded


def select_current_replayable_pairs(
    groups: dict[str, _GroupInfo],
    include_decrees: bool = True,
) -> tuple[list[tuple[str, str, str, int, str, str, str, int, int, str]], dict[str, int]]:
    """Select one latest/current replay pair for each amended structured RT group.

    The public Estonia divergence viewer is about current consolidated text, not
    every historical adjacent version.  A group is replayable here when it has:

    - an allowed schema;
    - at least one amendment marker;
    - at least two structured consolidated versions;
    - a penultimate base and latest oracle version.
    """
    allowed = _LAW_SCHEMAS | (_DECREE_SCHEMAS if include_decrees else frozenset())
    pairs: list[tuple[str, str, str, int, str, str, str, int, int, str]] = []
    excluded: dict[str, int] = defaultdict(int)

    for gid, group in groups.items():
        if not group.schemas & allowed:
            excluded["schema_not_allowed"] += 1
            continue
        if group.n_amendments <= 0:
            excluded["no_amendments"] += 1
            continue
        tvs = sorted(group.terviktekst_with_body, key=lambda item: (item[2], item[0]))
        if len(tvs) < 2:
            excluded["fewer_than_2_tervikteksts"] += 1
            continue
        schema = "unknown"
        for preferred in ("tyviseadus", "muutmisseadus", "maarus", "muutmismaarus", "juurakt"):
            if preferred in group.schemas:
                schema = preferred
                break
        base_id, _, base_effective = tvs[-2]
        oracle_id, _, oracle_effective = tvs[-1]
        version_count = len(tvs)
        pairs.append(
            (
                gid,
                base_id,
                oracle_id,
                group.n_amendments,
                schema,
                base_effective,
                oracle_effective,
                version_count - 1,
                version_count,
                group.title,
            )
        )

    pairs.sort(key=lambda item: (item[4], item[9], item[0]))
    return pairs, excluded


def select_replayable_pairs(
    groups: dict[str, _GroupInfo],
    include_decrees: bool = True,
) -> tuple[list[tuple[str, str, str, int, str, str, str, int, int, str]], dict[str, int]]:
    """Select every consecutive replayable RT consolidated-version pair.

    This is intentionally broader than the benchmark corpus.  The benchmark
    selects one latest pair per group; the publication/review corpus needs every
    adjacent state transition LawVM can replay and compare.
    """
    allowed = _LAW_SCHEMAS | (_DECREE_SCHEMAS if include_decrees else frozenset())
    pairs: list[tuple[str, str, str, int, str, str, str, int, int, str]] = []
    excluded: dict[str, int] = defaultdict(int)

    for gid, group in groups.items():
        if not group.schemas & allowed:
            excluded["schema_not_allowed"] += 1
            continue
        tvs = sorted(group.terviktekst_with_body, key=lambda item: (item[2], item[0]))
        if len(tvs) < 2:
            excluded["fewer_than_2_tervikteksts"] += 1
            continue
        schema = "unknown"
        for preferred in ("tyviseadus", "muutmisseadus", "maarus", "muutmismaarus", "juurakt"):
            if preferred in group.schemas:
                schema = preferred
                break
        version_count = len(tvs)
        for version_index, (base, oracle) in enumerate(zip(tvs, tvs[1:]), start=1):
            base_id, _, base_effective = base
            oracle_id, _, oracle_effective = oracle
            pairs.append(
                (
                    gid,
                    base_id,
                    oracle_id,
                    group.n_amendments,
                    schema,
                    base_effective,
                    oracle_effective,
                    version_index,
                    version_count,
                    group.title,
                )
            )

    pairs.sort(key=lambda item: (item[0], item[7]))
    return pairs, excluded


def summarize_pairs(
    pairs: list[tuple[str, str, str, int, str]],
) -> tuple[dict[str, int], int, int, dict[str, int]]:
    """Summarize curated EE pair set by schema and amendment bucket."""
    schema_counts: dict[str, int] = defaultdict(int)
    amend_buckets: dict[str, int] = defaultdict(int)
    n_laws = 0
    n_decrees = 0

    for _, _, _, n_amendments, schema in pairs:
        schema_counts[schema] += 1
        if schema in _LAW_SCHEMAS:
            n_laws += 1
        elif schema in _DECREE_SCHEMAS:
            n_decrees += 1

        if n_amendments == 0:
            bucket = "0"
        elif n_amendments == 1:
            bucket = "1"
        elif n_amendments <= 3:
            bucket = "2-3"
        elif n_amendments <= 10:
            bucket = "4-10"
        elif n_amendments <= 50:
            bucket = "11-50"
        else:
            bucket = "51+"
        amend_buckets[bucket] += 1

    return dict(schema_counts), n_laws, n_decrees, dict(amend_buckets)


def _write_notes(
    pairs: list[tuple[str, str, str, int, str]],
    groups: dict[str, _GroupInfo],
    excluded: dict[str, int],
    schema_counts: dict[str, int],
    amend_buckets: dict[str, int],
    include_decrees: bool,
    notes_path: Path,
) -> None:
    n_law = sum(v for k, v in schema_counts.items() if k in _LAW_SCHEMAS)
    n_decree = sum(v for k, v in schema_counts.items() if k in _DECREE_SCHEMAS)
    n_with_amends = sum(1 for _, _, _, na, _ in pairs if na >= 1)
    notes = f"""# EE Corpus Curation Notes

**Date**: {time.strftime("%Y-%m-%d")}
**Archive**: data/ee_riigiteataja.farchive

## Summary

- Source: Riigi Teataja Farchive — {len(groups)} unique terviktekstiGrupiID groups
- **Final corpus: {len(pairs)} (base, oracle) pairs**
- Laws (tyviseadus/muutmisseadus): {n_law}
- Decrees (maarus/muutmismaarus/juurakt): {n_decree}
- Pairs with >= 1 amendment: {n_with_amends}

## Selection Criteria

1. Schema filter.
   {"Laws + decrees included." if include_decrees else "Laws only (tyviseadus/muutmisseadus)."}
2. Body content: terviktekst must contain `<peatykk>` or `<paragrahv>` elements and
   be at least {_MIN_BODY_BYTES} bytes.
3. 2+ tervikteksts per group.
4. Pair selection: (second-to-last, last) by `kehtivuseAlgus`.

## Exclusion Stats

| Reason | Count |
|--------|-------|
"""
    for reason, count in sorted(excluded.items(), key=lambda item: -item[1]):
        notes += f"| {reason} | {count} |\n"
    notes += "\n## Distribution by Schema\n\n| Schema | Count |\n|--------|-------|\n"
    for schema, count in sorted(schema_counts.items(), key=lambda item: -item[1]):
        notes += f"| {schema} | {count} |\n"
    notes += "\n## Distribution by Amendment Count\n\n| Bucket | Count |\n|--------|-------|\n"
    for bucket in ["0", "1", "2-3", "4-10", "11-50", "51+"]:
        if bucket in amend_buckets:
            notes += f"| {bucket} | {amend_buckets[bucket]} |\n"
    notes_path.write_text(notes, encoding="utf-8")


def _write_replayable_notes(
    pairs: list[tuple[str, str, str, int, str, str, str, int, int, str]],
    groups: dict[str, _GroupInfo],
    excluded: dict[str, int],
    schema_counts: dict[str, int],
    amend_buckets: dict[str, int],
    include_decrees: bool,
    notes_path: Path,
) -> None:
    n_law = sum(v for k, v in schema_counts.items() if k in _LAW_SCHEMAS)
    n_decree = sum(v for k, v in schema_counts.items() if k in _DECREE_SCHEMAS)
    group_count = len({gid for gid, *_ in pairs})
    notes = f"""# EE Replayable Corpus Notes

**Date**: {time.strftime("%Y-%m-%d")}
**Archive**: data/ee_riigiteataja.farchive

## Summary

- Source: Riigi Teataja Farchive — {len(groups)} unique terviktekstiGrupiID groups
- **Replayable corpus: {len(pairs)} consecutive (base, oracle) version-comparison cases**
- Groups represented: {group_count}
- Laws (tyviseadus/muutmisseadus pairs): {n_law}
- Decrees (maarus/muutmismaarus/juurakt pairs): {n_decree}

## Selection Criteria

1. Schema filter.
   {"Laws + decrees included." if include_decrees else "Laws only (tyviseadus/muutmisseadus)."}
2. Body content: each terviktekst must contain `<peatykk>` or `<paragrahv>` elements and
   be at least {_MIN_BODY_BYTES} bytes.
3. 2+ structured tervikteksts per group.
4. Pair selection: every consecutive consolidated-version pair by `kehtivuseAlgus`.

This corpus is for historical adjacent-version replay review. The public
current divergence surface should use `current_replayable_corpus.csv`.

## Exclusion Stats

| Reason | Count |
|--------|-------|
"""
    for reason, count in sorted(excluded.items(), key=lambda item: -item[1]):
        notes += f"| {reason} | {count} |\n"
    notes += "\n## Distribution by Schema\n\n| Schema | Count |\n|--------|-------|\n"
    for schema, count in sorted(schema_counts.items(), key=lambda item: -item[1]):
        notes += f"| {schema} | {count} |\n"
    notes += "\n## Distribution by Amendment Count\n\n| Bucket | Count |\n|--------|-------|\n"
    for bucket in ["0", "1", "2-3", "4-10", "11-50", "51+"]:
        if bucket in amend_buckets:
            notes += f"| {bucket} | {amend_buckets[bucket]} |\n"
    notes_path.write_text(notes, encoding="utf-8")


def _write_current_replayable_notes(
    pairs: list[tuple[str, str, str, int, str, str, str, int, int, str]],
    groups: dict[str, _GroupInfo],
    excluded: dict[str, int],
    schema_counts: dict[str, int],
    amend_buckets: dict[str, int],
    include_decrees: bool,
    notes_path: Path,
) -> None:
    n_law = sum(v for k, v in schema_counts.items() if k in _LAW_SCHEMAS)
    n_decree = sum(v for k, v in schema_counts.items() if k in _DECREE_SCHEMAS)
    notes = f"""# EE Current Replayable Corpus Notes

**Date**: {time.strftime("%Y-%m-%d")}
**Archive**: data/ee_riigiteataja.farchive

## Summary

- Source: Riigi Teataja Farchive — {len(groups)} unique terviktekstiGrupiID groups
- **Current replayable corpus: {len(pairs)} latest-version comparison cases**
- Laws (tyviseadus/muutmisseadus pairs): {n_law}
- Decrees (maarus/muutmismaarus/juurakt pairs): {n_decree}

## Selection Criteria

1. Schema filter.
   {"Laws + decrees included." if include_decrees else "Laws only (tyviseadus/muutmisseadus)."}
2. Amendment history: group must have at least one `<muutmismarge>`.
3. Body content: each terviktekst must contain `<peatykk>` or `<paragrahv>` elements and
   be at least {_MIN_BODY_BYTES} bytes.
4. 2+ structured tervikteksts per group.
5. Pair selection: penultimate structured consolidated version as base, latest
   structured consolidated version as current oracle, by `kehtivuseAlgus`.

This corpus is the public Estonia divergence browser input. It intentionally
does not expose every historical adjacent version pair.

## Exclusion Stats

| Reason | Count |
|--------|-------|
"""
    for reason, count in sorted(excluded.items(), key=lambda item: -item[1]):
        notes += f"| {reason} | {count} |\n"
    notes += "\n## Distribution by Schema\n\n| Schema | Count |\n|--------|-------|\n"
    for schema, count in sorted(schema_counts.items(), key=lambda item: -item[1]):
        notes += f"| {schema} | {count} |\n"
    notes += "\n## Distribution by Amendment Count\n\n| Bucket | Count |\n|--------|-------|\n"
    for bucket in ["0", "1", "2-3", "4-10", "11-50", "51+"]:
        if bucket in amend_buckets:
            notes += f"| {bucket} | {amend_buckets[bucket]} |\n"
    notes_path.write_text(notes, encoding="utf-8")


def run_acquire(args: "argparse.Namespace") -> None:
    parts = [part.strip() for part in args.parts.split(",") if part.strip()]
    db_path = Path(args.db)
    if args.phase is None or args.phase == 1:
        acts_by_part = phase1_discover(db_path, parts, args.delay)
        all_acts = sorted(set(aid for aids in acts_by_part.values() for aid in aids))
    else:
        acts_by_part = phase1_discover(db_path, parts, delay=0)
        all_acts = sorted(set(aid for aids in acts_by_part.values() for aid in aids))
    if args.phase is None or args.phase == 2:
        if not all_acts:
            raise SystemExit("No acts to fetch. Run phase 1 first.")
        phase2_fetch(db_path, all_acts, args.workers, args.delay)


def run_curate(args: "argparse.Namespace") -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: archive not found: {db_path}", file=sys.stderr)
        raise SystemExit(1)

    csv_path = Path(getattr(args, "output_csv", "") or _OUT_CSV)
    notes_path = Path(getattr(args, "output_notes", "") or _OUT_NOTES)
    include_decrees = not getattr(args, "laws_only", False)

    archive = open_rt_archive(db_path)
    try:
        groups = build_index(archive)
        print(f"\nSelecting pairs (include_decrees={include_decrees})...")
        pairs, excluded = select_pairs(groups, include_decrees=include_decrees)
        print(f"Selected: {len(pairs)} pairs")
        print("Excluded:")
        for reason, count in sorted(excluded.items(), key=lambda item: -item[1]):
            print(f"  {reason}: {count}")
    finally:
        archive.close()

    schema_counts, n_laws, n_decrees, amend_buckets = summarize_pairs(pairs)
    print("\nBy schema:")
    for schema, count in sorted(schema_counts.items(), key=lambda item: -item[1]):
        print(f"  {schema}: {count}")
    print(f"Laws: {n_laws}, Decrees: {n_decrees}")

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["grupi_id", "base_id", "oracle_id", "n_amendments", "schema"])
        for gid, bid, oid, n_amendments, schema in pairs:
            writer.writerow([gid, bid, oid, n_amendments, schema])
    print(f"\nWritten: {csv_path}")

    _write_notes(
        pairs,
        groups,
        excluded,
        schema_counts,
        amend_buckets,
        include_decrees=include_decrees,
        notes_path=notes_path,
    )
    print(f"Written: {notes_path}")


def run_replayable(args: "argparse.Namespace") -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: archive not found: {db_path}", file=sys.stderr)
        raise SystemExit(1)

    csv_path = Path(getattr(args, "output_csv", "") or _REPLAYABLE_CSV)
    notes_path = Path(getattr(args, "output_notes", "") or _REPLAYABLE_NOTES)
    include_decrees = not getattr(args, "laws_only", False)

    archive = open_rt_archive(db_path)
    try:
        groups = build_index(archive)
        print(f"\nSelecting all consecutive replayable pairs (include_decrees={include_decrees})...")
        pairs, excluded = select_replayable_pairs(groups, include_decrees=include_decrees)
        print(f"Selected: {len(pairs)} replayable pairs")
        print("Excluded:")
        for reason, count in sorted(excluded.items(), key=lambda item: -item[1]):
            print(f"  {reason}: {count}")
    finally:
        archive.close()

    schema_counts, n_laws, n_decrees, amend_buckets = summarize_pairs(
        [(gid, bid, oid, na, schema) for gid, bid, oid, na, schema, *_ in pairs]
    )
    print("\nBy schema:")
    for schema, count in sorted(schema_counts.items(), key=lambda item: -item[1]):
        print(f"  {schema}: {count}")
    print(f"Laws: {n_laws}, Decrees: {n_decrees}")

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "grupi_id",
                "base_id",
                "oracle_id",
                "n_amendments",
                "schema",
                "base_effective",
                "oracle_effective",
                "version_index",
                "version_count",
                "title",
            ]
        )
        for row in pairs:
            writer.writerow(row)
    print(f"\nWritten: {csv_path}")

    _write_replayable_notes(
        pairs,
        groups,
        excluded,
        schema_counts,
        amend_buckets,
        include_decrees=include_decrees,
        notes_path=notes_path,
    )
    print(f"Written: {notes_path}")


def run_current(args: "argparse.Namespace") -> None:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: archive not found: {db_path}", file=sys.stderr)
        raise SystemExit(1)

    csv_path = Path(getattr(args, "output_csv", "") or _CURRENT_CSV)
    notes_path = Path(getattr(args, "output_notes", "") or _CURRENT_NOTES)
    include_decrees = not getattr(args, "laws_only", False)

    archive = open_rt_archive(db_path)
    try:
        groups = build_index(archive)
        print(f"\nSelecting current replayable amended pairs (include_decrees={include_decrees})...")
        pairs, excluded = select_current_replayable_pairs(groups, include_decrees=include_decrees)
        print(f"Selected: {len(pairs)} current replayable pairs")
        print("Excluded:")
        for reason, count in sorted(excluded.items(), key=lambda item: -item[1]):
            print(f"  {reason}: {count}")
    finally:
        archive.close()

    schema_counts, n_laws, n_decrees, amend_buckets = summarize_pairs(
        [(gid, bid, oid, na, schema) for gid, bid, oid, na, schema, *_ in pairs]
    )
    print("\nBy schema:")
    for schema, count in sorted(schema_counts.items(), key=lambda item: -item[1]):
        print(f"  {schema}: {count}")
    print(f"Laws: {n_laws}, Decrees: {n_decrees}")

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "grupi_id",
                "base_id",
                "oracle_id",
                "n_amendments",
                "schema",
                "base_effective",
                "oracle_effective",
                "version_index",
                "version_count",
                "title",
            ]
        )
        for row in pairs:
            writer.writerow(row)
    print(f"\nWritten: {csv_path}")

    _write_current_replayable_notes(
        pairs,
        groups,
        excluded,
        schema_counts,
        amend_buckets,
        include_decrees=include_decrees,
        notes_path=notes_path,
    )
    print(f"Written: {notes_path}")


def run_stats(args: "argparse.Namespace") -> None:
    """Show EE archive statistics without re-indexing."""
    import json as _json

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: archive not found: {db_path}", file=sys.stderr)
        raise SystemExit(1)

    from farchive import Farchive
    archive = Farchive(db_path)
    try:
        conn = archive._conn

        try:
            archive_stats = archive.stats()
            n_urls = archive_stats.locator_count
            n_blobs = archive_stats.blob_count
            raw_mb = archive_stats.total_raw_bytes / 1e6
            stored_mb = archive_stats.total_stored_bytes / 1e6
        except Exception:
            n_urls, n_blobs, raw_mb, stored_mb = _safe_archive_size_stats(conn)

        locator_table = _locator_table_name(conn)

        # Count RT acts
        rt_rows = conn.execute(
            f"SELECT COUNT(*) FROM {locator_table} WHERE locator LIKE '%riigiteataja.ee/akt/%.xml'"
        ).fetchone()
        n_rt_obs = rt_rows[0] if rt_rows else 0

        # Count unique act IDs
        grupi_rows = conn.execute(
            f"SELECT COUNT(DISTINCT substr(locator, instr(locator, '/akt/') + 5)) "
            f"FROM {locator_table} WHERE locator LIKE '%riigiteataja.ee/akt/%.xml'"
        ).fetchone()
        n_unique_acts = grupi_rows[0] if grupi_rows else 0

        # Schema distribution
        schema_counts: dict[str, int] = defaultdict(int)
        rows = conn.execute(
            f"SELECT locator FROM {locator_table} WHERE locator LIKE '%riigiteataja.ee/akt/%.xml' LIMIT 10000"
        ).fetchall()
        for (url,) in rows:
            data = archive.get(url)
            if not data or len(data) < 100:
                continue
            m_ns = re.search(rb'xmlns\s*=\s*["\x27]([^"\x27]+)', data[:2000])
            ns = m_ns.group(1).decode("utf-8", errors="replace") if m_ns else ""
            schema = _classify_schema(ns)
            if schema:
                schema_counts[schema] += 1

        payload = {
            "archive_path": str(db_path),
            "urls": n_urls,
            "blobs": n_blobs,
            "raw_mb": round(raw_mb, 1),
            "stored_mb": round(stored_mb, 1),
            "rt_observations": n_rt_obs,
            "unique_act_ids": n_unique_acts,
            "schema_distribution": dict(schema_counts),
        }
    finally:
        archive.close()

    if getattr(args, "json", False):
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print("\n=== EE Archive Statistics ===")
    print(f"  Archive path : {payload['archive_path']}")
    print(f"  URLs tracked : {payload['urls']:,}")
    print(f"  Blobs stored : {payload['blobs']:,}")
    print(f"  Raw size     : {payload['raw_mb']:.1f} MB")
    print(f"  Stored size  : {payload['stored_mb']:.1f} MB")
    print(f"  RT acts      : {payload['rt_observations']:,} observations")
    print(f"  Unique acts  : {payload['unique_act_ids']:,}")
    if payload["schema_distribution"]:
        print("\n  Schema distribution (sample):")
        for schema, count in sorted(payload["schema_distribution"].items(), key=lambda x: -x[1]):
            print(f"    {schema}: {count}")


def main(args: "argparse.Namespace") -> None:
    command = getattr(args, "ee_corpus_command", "")
    if command == "acquire":
        run_acquire(args)
        return
    if command == "curate":
        run_curate(args)
        return
    if command == "current":
        run_current(args)
        return
    if command == "replayable":
        run_replayable(args)
        return
    if command == "stats":
        run_stats(args)
        return
    raise SystemExit(f"Unknown ee-corpus subcommand: {command}")


__all__ = [
    "build_index",
    "main",
    "phase1_discover",
    "phase2_fetch",
    "run_acquire",
    "run_curate",
    "select_pairs",
    "summarize_pairs",
]

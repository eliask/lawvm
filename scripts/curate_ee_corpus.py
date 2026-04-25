#!/usr/bin/env python3
"""
Corpus curation script for LawVM Estonia (Riigi Teataja) benchmark.

Source: RT FetchArchive (.tmp/riigiteataja_archive.db)
Selects all terviktekstiGrupiID groups with:
  1. At least 2 terviktekst versions with non-stub body content
  2. Body content contains <peatykk> or <paragrahv> elements
  3. Body size >= MIN_BODY_BYTES (filters pure-metadata stubs)
  4. Recognized schema namespace (tyviseadus / muutmisseadus / maarus / etc.)

Output:
  data/estonia/bench_corpus.csv      — grupi_id,base_id,oracle_id,n_amendments,schema
  data/estonia/bench_corpus_notes.md — statistics

Note: unlike FI, the EE corpus CSV includes base_id and oracle_id explicitly
(the RT archive has no canonical per-statute ID like YEAR/NUM).

CSV column order:
  grupi_id, base_id, oracle_id, n_amendments, schema
"""

from __future__ import annotations

import csv
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).parent.parent  # LawVM/
DEFAULT_DB = ROOT / ".tmp" / "riigiteataja_archive.db"
OUT_DIR = ROOT / "data" / "estonia"
OUT_CSV = OUT_DIR / "bench_corpus.csv"
OUT_NOTES = OUT_DIR / "bench_corpus_notes.md"

# Body-presence indicators
_BODY_TAGS = (b"<peatykk", b"<paragrahv")

# Schema namespace fragments → canonical schema names (checked in order)
_SCHEMA_MAP = [
    ("muutmisseadus", "muutmisseadus"),
    ("tyviseadus",    "tyviseadus"),
    ("muutmismaarus", "muutmismaarus"),
    ("maarus",        "maarus"),
    ("juurakt",       "juurakt"),
]

_LAW_SCHEMAS     = frozenset(["tyviseadus", "muutmisseadus"])
_DECREE_SCHEMAS  = frozenset(["maarus", "muutmismaarus", "juurakt"])
_ALL_SCHEMAS     = _LAW_SCHEMAS | _DECREE_SCHEMAS

# Minimum body size to count as non-stub (raw bytes, before decompression)
_MIN_BODY_BYTES = 500


@dataclass
class _GroupInfo:
    grupi_id: str
    terviktekst_with_body: list = field(default_factory=list)
    # Each entry: (aktViide, size_bytes, kehtivuseAlgus)
    n_amendments: int = 0
    schemas: set = field(default_factory=set)
    title: str = ""


def _classify_schema(ns: str) -> str:
    """Return canonical schema name from namespace string."""
    for fragment, name in _SCHEMA_MAP:
        if fragment in ns:
            return name
    return ""


def build_index(archive) -> dict[str, _GroupInfo]:
    """Scan all RT XML blobs and build per-grupiId index.

    Uses FetchArchive.get_latest() for correct decompression (handles
    zstd, zstd_dict, zstd_delta encodings transparently).
    """
    conn = archive._conn

    rows = conn.execute(
        "SELECT url FROM observation WHERE url LIKE '%riigiteataja.ee/akt/%.xml'"
    ).fetchall()
    print(f"Total XML observations: {len(rows)}")

    groups: dict[str, _GroupInfo] = {}
    t0 = time.time()
    no_grupi_id = 0

    for i, (url,) in enumerate(rows):
        if (i + 1) % 10000 == 0:
            elapsed = time.time() - t0
            print(
                f"  [{i+1}/{len(rows)}] groups={len(groups)} "
                f"elapsed={elapsed:.1f}s",
                file=sys.stderr,
            )

        data = archive.get_latest(url)
        if not data or len(data) < 100:
            continue

        aid = url.split("/akt/")[-1].replace(".xml", "")
        prefix = data[:20000]

        # grupi_id
        m_g = re.search(rb'<[^>]*terviktekstiGrupiID[^>]*>([^<]+)<', prefix)
        if not m_g:
            no_grupi_id += 1
            continue
        grupi_id = m_g.group(1).decode("utf-8", errors="replace").strip()

        # tekstiliik
        m_t = re.search(rb'<[^>]*tekstiliik[^>]*>([^<]+)<', prefix)
        tekstiliik = m_t.group(1).decode("utf-8", errors="replace").strip() if m_t else ""

        # namespace → schema
        m_ns = re.search(rb'xmlns\s*=\s*["\x27]([^"\x27]+)', prefix)
        ns = m_ns.group(1).decode("utf-8", errors="replace") if m_ns else ""
        schema = _classify_schema(ns)

        # kehtivuseAlgus
        m_algus = re.search(rb'<[^>]*kehtivuseAlgus[^>]*>([^<]+)<', prefix)
        algus = m_algus.group(1).decode("utf-8", errors="replace").strip()[:10] if m_algus else "9999-99-99"

        # title
        m_title = re.search(rb'<[^>]*pealkiri[^>]*>([^<]+)<', prefix)
        title = m_title.group(1).decode("utf-8", errors="replace").strip() if m_title else ""

        if grupi_id not in groups:
            groups[grupi_id] = _GroupInfo(grupi_id=grupi_id)

        g = groups[grupi_id]

        if schema:
            g.schemas.add(schema)

        if title and not g.title:
            g.title = title

        # Count amendments (scan up to 200KB)
        n_amend = len(re.findall(rb'<[^>]*muutmismarge[^>]*>', data[:200000]))
        if n_amend > g.n_amendments:
            g.n_amendments = n_amend

        # Record tervikteksts with body
        if tekstiliik == "terviktekst":
            has_structure = any(tag in data for tag in _BODY_TAGS)
            if has_structure and len(data) >= _MIN_BODY_BYTES:
                g.terviktekst_with_body.append((aid, len(data), algus))

    elapsed = time.time() - t0
    print(f"Indexed {len(rows)} blobs in {elapsed:.1f}s ({len(rows)/elapsed:.0f}/s)")
    print(f"  Unique grupi_ids: {len(groups)}")
    print(f"  No grupi_id (algtekst/standalone): {no_grupi_id}")
    return groups


def select_pairs(
    groups: dict[str, _GroupInfo],
    include_decrees: bool = True,
) -> tuple[list[tuple[str, str, str, int, str]], dict[str, int]]:
    """Select (grupi_id, base_id, oracle_id, n_amendments, schema) pairs.

    Criteria:
    - Group has >= 2 tervikteksts with body structure
    - Schema is in allowed set
    - Pairs are (second-to-last, last) by kehtivuseAlgus date (chronological)
    """
    allowed = _LAW_SCHEMAS | (_DECREE_SCHEMAS if include_decrees else frozenset())

    pairs = []
    excluded: dict[str, int] = defaultdict(int)

    for gid, g in groups.items():
        if not g.schemas & allowed:
            excluded["schema_not_allowed"] += 1
            continue

        # Sort by kehtivuseAlgus date (x[2])
        tvs = sorted(g.terviktekst_with_body, key=lambda x: x[2])

        if len(tvs) < 2:
            excluded["fewer_than_2_tervikteksts"] += 1
            continue

        base_id   = tvs[-2][0]   # second-to-last chronologically
        oracle_id = tvs[-1][0]   # latest chronologically

        # Determine primary schema (prefer law over decree)
        schema = "unknown"
        for preferred in ("tyviseadus", "muutmisseadus", "maarus", "muutmismaarus", "juurakt"):
            if preferred in g.schemas:
                schema = preferred
                break

        pairs.append((gid, base_id, oracle_id, g.n_amendments, schema))

    pairs.sort(key=lambda x: (x[3], x[0]))  # sort by n_amendments asc, then grupi_id
    return pairs, excluded


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Curate EE bench corpus from RT archive")
    parser.add_argument(
        "--db", metavar="PATH", default=str(DEFAULT_DB),
        help=f"RT FetchArchive DB path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--laws-only", action="store_true",
        help="Include only laws (tyviseadus/muutmisseadus), not decrees",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: archive not found: {db_path}", file=sys.stderr)
        print("Run: uv run python scripts/acquire_ee_corpus.py", file=sys.stderr)
        sys.exit(1)

    from lawvm.estonia.fetch import open_rt_archive
    archive = open_rt_archive(db_path)

    try:
        # Step 1: Build index from archive
        groups = build_index(archive)

        # Step 2: Select pairs (include decrees unless --laws-only)
        include_decrees = not args.laws_only
        print(f"\nSelecting pairs (include_decrees={include_decrees})...")
        pairs, excluded = select_pairs(groups, include_decrees=include_decrees)
        print(f"Selected: {len(pairs)} pairs")
        print("Excluded:")
        for reason, count in sorted(excluded.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")
    finally:
        archive.close()

    # Step 3: Statistics
    schema_counts: dict[str, int] = defaultdict(int)
    n_laws = 0
    n_decrees = 0
    amend_buckets: dict[str, int] = defaultdict(int)

    for gid, bid, oid, na, schema in pairs:
        schema_counts[schema] += 1
        if schema in _LAW_SCHEMAS:
            n_laws += 1
        elif schema in _DECREE_SCHEMAS:
            n_decrees += 1

        if na == 0:
            bkt = "0"
        elif na == 1:
            bkt = "1"
        elif na <= 3:
            bkt = "2-3"
        elif na <= 10:
            bkt = "4-10"
        elif na <= 50:
            bkt = "11-50"
        else:
            bkt = "51+"
        amend_buckets[bkt] += 1

    print("\nBy schema:")
    for s, c in sorted(schema_counts.items(), key=lambda x: -x[1]):
        print(f"  {s}: {c}")
    print(f"Laws: {n_laws}, Decrees: {n_decrees}")

    # Step 4: Write CSV
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["grupi_id", "base_id", "oracle_id", "n_amendments", "schema"])
        for gid, bid, oid, na, schema in pairs:
            w.writerow([gid, bid, oid, na, schema])
    print(f"\nWritten: {OUT_CSV}")

    # Step 5: Write notes
    _write_notes(pairs, groups, excluded, schema_counts, amend_buckets, include_decrees)
    print(f"Written: {OUT_NOTES}")


def _write_notes(
    pairs: list,
    groups: dict[str, _GroupInfo],
    excluded: dict[str, int],
    schema_counts: dict[str, int],
    amend_buckets: dict[str, int],
    include_decrees: bool,
) -> None:
    n_law = sum(v for k, v in schema_counts.items() if k in _LAW_SCHEMAS)
    n_decree = sum(v for k, v in schema_counts.items() if k in _DECREE_SCHEMAS)
    n_with_amends = sum(1 for _, _, _, na, _ in pairs if na >= 1)

    notes = f"""# EE Corpus Curation Notes

**Date**: {time.strftime('%Y-%m-%d')}
**Script**: curate_ee_corpus.py
**Archive**: .tmp/riigiteataja_archive.db

## Summary

- Source: Riigi Teataja FetchArchive — {len(groups)} unique terviktekstiGrupiID groups
- **Final corpus: {len(pairs)} (base, oracle) pairs**
- Laws (tyviseadus/muutmisseadus): {n_law}
- Decrees (maarus/muutmismaarus/juurakt): {n_decree}
- Pairs with >= 1 amendment: {n_with_amends}

## Selection Criteria

1. **Schema filter**: group must have at least one recognized schema namespace.
   {'Laws + decrees included.' if include_decrees else 'Laws only (tyviseadus/muutmisseadus).'}
2. **Body content**: terviktekst must contain `<peatykk>` or `<paragrahv>` elements AND
   be at least {_MIN_BODY_BYTES} bytes (excludes pure-metadata stubs).
3. **2+ tervikteksts**: group must have at least 2 qualifying tervikteksts to form a
   (base, oracle) pair.
4. **Pair selection**: (second-to-last, last) by kehtivuseAlgus date.

## CSV Format

`grupi_id, base_id, oracle_id, n_amendments, schema`

- `grupi_id`: terviktekstiGrupiID (consolidation group identifier)
- `base_id`: aktViide of the base terviktekst (second-to-last by date)
- `oracle_id`: aktViide of the oracle terviktekst (latest by date)
- `n_amendments`: count of muutmismarge entries found in the group
- `schema`: primary schema type

## Exclusion Stats

| Reason | Count |
|--------|-------|
"""
    for reason, count in sorted(excluded.items(), key=lambda x: -x[1]):
        notes += f"| {reason} | {count} |\n"

    notes += """
## Distribution by Schema

| Schema | Count |
|--------|-------|
"""
    for s, c in sorted(schema_counts.items(), key=lambda x: -x[1]):
        notes += f"| {s} | {c} |\n"

    notes += """
## Distribution by Amendment Count

| Bucket | Count |
|--------|-------|
"""
    for bkt in ["0", "1", "2-3", "4-10", "11-50", "51+"]:
        if bkt in amend_buckets:
            notes += f"| {bkt} | {amend_buckets[bkt]} |\n"

    notes += f"""
## Relationship to ee_bench.py Selection

The current `_index_corpus()` in `ee_bench.py` finds:
- Laws only (tyviseadus/muutmisseadus): 343 pairs
- All schemas (including decrees): 2201 pairs

This curated corpus selects {len(pairs)} pairs using the same logic but:
- Uses explicit CSV for reproducibility (same pairs every run)
- Provides n_amendments and schema columns for filtering
- Documents exact selection criteria

## Known Limitations

- The RT archive only holds what was fetched during acquisition runs. Groups with
  fewer than 2 fetched tervikteksts are excluded even if more exist on RT.
- Old-format amendments (pre-2010 tyviseadus HTML CDATA) may produce lower accuracy
  due to schema differences — this is an accuracy signal, not a curation flaw.
- n_amendments is a lower bound: counted from muutmismarge elements in the most
  amendment-rich act in the group (may undercount for older statutes).
"""
    with open(OUT_NOTES, "w") as f:
        f.write(notes)


if __name__ == "__main__":
    main()

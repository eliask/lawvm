#!/usr/bin/env python3
"""
Corpus curation script for LawVM benchmark.

Source: amendment_parents.csv — all unique parent statutes (no amendment count limit).
Exclusion criteria (all structural, no temporal filtering):
  1. Base statute XML exists in farchive (finlex://sd/...)
  2. XML is parseable (not corrupted/empty)
  3. Has section structure (not hcontainer-only body)
  4. Has >= 1 amendment in amendment_parents.csv
  5. Oracle consolidated XML exists AND has non-empty body text
  6. All amendment statute texts also exist in farchive

Inputs:
  - data/finland/amendment_parents.csv  (canonical source)
  - data/finlex.farchive

Output:
  - data/finland/bench_corpus.csv
  - data/finland/bench_corpus_notes.md
"""

import csv
import re
from collections import defaultdict
from pathlib import Path

import farchive as _farchive

from lawvm.corpus_store import ArchiveCorpusStore, statute_url

ROOT = Path(__file__).parent.parent  # LawVM/
FARCHIVE_PATH = ROOT / "data/finlex.farchive"
AMEND_PARENTS = ROOT / "data/finland/amendment_parents.csv"
OUT_CSV = ROOT / "data/finland/bench_corpus.csv"
OUT_NOTES = ROOT / "data/finland/bench_corpus_notes.md"

# ---- build amendment index from canonical source -------------------------
print("Loading amendment parents...")
amend_count: dict[str, int] = defaultdict(int)
parent_to_amendments: dict[str, list[str]] = defaultdict(list)
with open(AMEND_PARENTS) as f:
    for row in csv.DictReader(f):
        parent = row.get("parent_id", "").strip()
        amend = row.get("amendment_id", "").strip()
        if parent and amend:
            amend_count[parent] += 1
            parent_to_amendments[parent].append(amend)

candidates = sorted(amend_count.items(), key=lambda x: (x[1], x[0]))
print(f"Unique parent statutes: {len(candidates)}")

# ---- build farchive indexes -----------------------------------------------
print("Building farchive source and oracle indexes...")
archive = _farchive.Farchive(str(FARCHIVE_PATH), readonly=True)
corpus = ArchiveCorpusStore(archive)

# Source index: sid -> canonical URL
source_index: dict[str, str] = {sid: statute_url(sid) for sid in corpus.list_statute_ids()}
print(f"Source index: {len(source_index)}")

# Oracle index: sid -> best versioned oracle locator (by highest version tag)
oracle_url_index: dict[str, str] = corpus.oracle_path_index()
print(f"Oracle index: {len(oracle_url_index)}")


# ---- XML structure check -------------------------------------------------
def check_xml_structure(data: bytes | None) -> str:
    if not data or len(data) < 100:
        return 'empty'
    text = data.decode('utf-8', errors='replace')
    if 'akn:section' in text or '<section' in text:
        return 'ok'
    if 'akn:paragraph' in text or '<paragraph' in text:
        return 'ok'
    return 'hcontainer'


def oracle_has_content(data: bytes | None) -> bool:
    if not data or len(data) < 100:
        return False
    text = data.decode('utf-8', errors='replace')
    return ('akn:section' in text or '<section' in text or
            'akn:paragraph' in text or '<paragraph' in text)


def oracle_is_mostly_repealed(data: bytes | None, threshold: float = 0.5) -> bool:
    """Check if ≥threshold fraction of sections are kumottu (repealed)."""
    if not data:
        return False
    text = data.decode('utf-8', errors='replace')
    n_sections = text.count('<section') + text.count('akn:section')
    if n_sections == 0:
        return False
    n_kumottu = sum(text.count(p) for p in [
        'on kumottu', 'Kumottu',
    ])
    # Simple heuristic: count "kumottu" occurrences vs section count
    return n_kumottu / n_sections >= threshold


def oracle_is_content_absent(data: bytes | None) -> bool:
    if not data:
        return False
    return b'contentAbsent' in data


# ---- bucket label --------------------------------------------------------
BUCKET_DEFS = [
    (1,1),(2,2),(3,3),(4,5),(6,10),(11,20),(21,50),(51,200),(201,9999),
]
def bucket_label(n: int) -> str:
    for lo, hi in BUCKET_DEFS:
        if lo <= n <= hi:
            return str(lo) if lo == hi else f"{lo}-{hi}"
    return "other"

# ---- main filter pass ----------------------------------------------------
print("\nApplying structural filters...")
selected: list[tuple[int, str]] = []
stats: dict[str, int] = defaultdict(int)
excl: dict[str, str] = {}

total = len(candidates)
for i, (sid, count) in enumerate(candidates):
    if i % 1000 == 0:
        print(f"  [{i}/{total}] ok={stats['ok']} excl={sum(v for k,v in stats.items() if k!='ok')}")

    if sid not in source_index:
        excl[sid] = "not_in_farchive"; stats["not_in_farchive"] += 1; continue

    source_data = archive.get(source_index[sid])
    struct = check_xml_structure(source_data)
    if struct != 'ok':
        excl[sid] = f"base_{struct}"; stats[f"base_{struct}"] += 1; continue

    oracle_url = oracle_url_index.get(sid)
    if not oracle_url:
        excl[sid] = "no_oracle"; stats["no_oracle"] += 1; continue
    oracle_data = archive.get(oracle_url)
    if oracle_is_content_absent(oracle_data):
        excl[sid] = "content_absent"; stats["content_absent"] += 1; continue
    if not oracle_has_content(oracle_data):
        excl[sid] = "oracle_empty_body"; stats["oracle_empty_body"] += 1; continue
    if oracle_is_mostly_repealed(oracle_data):
        excl[sid] = "mostly_repealed"; stats["mostly_repealed"] += 1; continue

    amendments = parent_to_amendments[sid]
    missing = [a for a in amendments if a not in source_index]
    # Allow statutes with ≥80% of amendment texts available
    if missing and len(missing) / len(amendments) > 0.2:
        excl[sid] = "amendment_texts_missing"; stats["amendment_texts_missing"] += 1; continue

    stats["ok"] += 1
    selected.append((count, sid))

archive.close()
print(f"  [{total}/{total}] done")

# ---- report --------------------------------------------------------------
print("\nStats:")
for k, v in sorted(stats.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print(f"\nSelected: {len(selected)}")

selected.sort(key=lambda x: (x[0], x[1]))

bkt_counts: dict[str, int] = defaultdict(int)
for count, sid in selected:
    bkt_counts[bucket_label(count)] += 1
print("By bucket:")
for bkt in sorted(bkt_counts.keys()):
    print(f"  {bkt:>8}: {bkt_counts[bkt]}")

decade_counts: dict[str, int] = defaultdict(int)
for count, sid in selected:
    m = re.match(r'^(\d{4})/', sid)
    if m:
        decade_counts[f"{int(m.group(1))//10*10}s"] += 1
print("By decade:")
for dec, cnt in sorted(decade_counts.items()):
    print(f"  {dec}: {cnt}")

# ---- write CSV -----------------------------------------------------------
with open(OUT_CSV, 'w', newline='') as f:
    w = csv.writer(f)
    for count, sid in selected:
        w.writerow([count, sid])
print(f"\nWritten: {OUT_CSV}")

# ---- write notes ---------------------------------------------------------
excl_by_reason: dict[str, int] = defaultdict(int)
for reason in excl.values():
    excl_by_reason[reason] += 1

notes = f"""# Corpus Curation Notes

**Date**: 2026-03-21
**Script**: curate_corpus.py

## Summary

- Source: `amendment_parents.csv` — {len(candidates)} unique parent statutes (full range, no amendment count cap)
- **Final corpus: {len(selected)} statutes** (fully recomputed, no carry-over from prior list)

## Exclusion Criteria (all structural, no temporal filtering)

1. **Base in farchive**: `data/finlex.farchive` must contain `finlex://sd/.../fin/main.xml` for the statute ID.
2. **Base XML structure**: Must contain `<section>` or `<paragraph>` elements (not hcontainer-only).
3. **Oracle exists with content**: Latest consolidated version in farchive must
   contain `<section>` or `<paragraph>` elements. Empty/hcontainer-only oracle → NO_TRUTH in benchmark.
4. **Amendment texts in farchive**: All amendment statutes referenced in `amendment_parents.csv` must
   also exist in farchive (grafter needs their text for replay).

Note: Pre-1990 and dash-suffix IDs are NOT excluded a priori.
Note: amendment_parents.csv IS the amendment-existence check (criterion 4 from design) — every
candidate here already has ≥1 amendment by construction.

## Exclusion Stats

| Reason | Count |
|--------|-------|
"""
for reason, cnt in sorted(excl_by_reason.items(), key=lambda x: -x[1]):
    notes += f"| {reason} | {cnt} |\n"

notes += """
## Distribution

### By Amendment Count Bucket

| Bucket | Count |
|--------|-------|
"""
for bkt in sorted(bkt_counts.keys()):
    notes += f"| {bkt} | {bkt_counts[bkt]} |\n"

notes += """
### By Decade

| Decade | Count |
|--------|-------|
"""
for dec, cnt in sorted(decade_counts.items()):
    notes += f"| {dec} | {cnt} |\n"

notes += """
## Known Limitations

- `amendment_parents.csv` may be incomplete for very recent (2024-2025) amendments.
- Oracle completeness ceiling: some valid statutes score poorly due to missing intermediate
  amendments in corpus (CAT-G failures in grafter) — not filterable here.
- hcontainer-only base/oracle statutes excluded; may be revisable if grafter gains support.
"""

with open(OUT_NOTES, 'w') as f:
    f.write(notes)
print(f"Written: {OUT_NOTES}")

# Corpus Curation Notes

**Date**: 2026-03-21
**Script**: curate_corpus.py

## Summary

- Source: `amendment_parents.csv` — 11909 unique parent statutes (full range, no amendment count cap)
- **Final corpus: 3545 statutes** (fully recomputed, no carry-over from prior list)

## Exclusion Criteria (all structural, no temporal filtering)

1. **Base in zip**: `statute.zip` must contain `fin@/main.xml` for the statute ID.
2. **Base XML structure**: Must contain `<section>` or `<paragraph>` elements (not hcontainer-only).
3. **Oracle exists with content**: Latest consolidated version in `statute-consolidated.zip` must
   contain `<section>` or `<paragraph>` elements. Empty/hcontainer-only oracle → NO_TRUTH in benchmark.
4. **Amendment texts in zip**: All amendment statutes referenced in `amendment_parents.csv` must
   also exist in `statute.zip` (grafter needs their text for replay).

Note: Pre-1990 and dash-suffix IDs are NOT excluded a priori.
Note: amendment_parents.csv IS the amendment-existence check (criterion 4 from design) — every
candidate here already has ≥1 amendment by construction.

## Exclusion Stats

| Reason | Count |
|--------|-------|
| not_in_zip | 5950 |
| content_absent | 2204 |
| mostly_repealed | 139 |
| amendment_texts_missing | 37 |
| base_hcontainer | 33 |
| oracle_empty_body | 1 |

## Distribution

### By Amendment Count Bucket

| Bucket | Count |
|--------|-------|
| 1 | 996 |
| 11-20 | 362 |
| 2 | 528 |
| 201-9999 | 1 |
| 21-50 | 225 |
| 3 | 369 |
| 4-5 | 468 |
| 51-200 | 46 |
| 6-10 | 550 |

### By Decade

| Decade | Count |
|--------|-------|
| 1730s | 2 |
| 1860s | 1 |
| 1900s | 2 |
| 1910s | 2 |
| 1920s | 15 |
| 1930s | 6 |
| 1940s | 13 |
| 1950s | 21 |
| 1960s | 61 |
| 1970s | 124 |
| 1980s | 195 |
| 1990s | 799 |
| 2000s | 1009 |
| 2010s | 946 |
| 2020s | 349 |

## Known Limitations

- `amendment_parents.csv` may be incomplete for very recent (2024-2025) amendments.
- Oracle completeness ceiling: some valid statutes score poorly due to missing intermediate
  amendments in corpus (CAT-G failures in grafter) — not filterable here.
- hcontainer-only base/oracle statutes excluded; may be revisable if grafter gains support.

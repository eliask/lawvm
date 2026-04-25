# EE Corpus Curation Notes

**Date**: 2026-03-29
**Archive**: .tmp/riigiteataja_archive.db

## Summary

- Source: Riigi Teataja FetchArchive — 22824 unique terviktekstiGrupiID groups
- **Final corpus: 343 (base, oracle) pairs**
- Laws (tyviseadus/muutmisseadus): 343
- Decrees (maarus/muutmismaarus/juurakt): 0
- Pairs with >= 1 amendment: 343

## Selection Criteria

1. Schema filter.
   Laws only (tyviseadus/muutmisseadus).
2. Body content: terviktekst must contain `<peatykk>` or `<paragrahv>` elements and
   be at least 500 bytes.
3. 2+ tervikteksts per group.
4. Pair selection: (second-to-last, last) by `kehtivuseAlgus`.

## Exclusion Stats

| Reason | Count |
|--------|-------|
| schema_not_allowed | 18135 |
| fewer_than_2_tervikteksts | 4346 |

## Distribution by Schema

| Schema | Count |
|--------|-------|
| tyviseadus | 343 |

## Distribution by Amendment Count

| Bucket | Count |
|--------|-------|
| 4-10 | 6 |
| 11-50 | 21 |
| 51+ | 316 |

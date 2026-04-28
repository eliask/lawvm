# EE Replayable Corpus Notes

**Date**: 2026-04-28
**Archive**: data/ee_riigiteataja.farchive

## Summary

- Source: Riigi Teataja Farchive — 22826 unique terviktekstiGrupiID groups
- **Replayable corpus: 3009 consecutive (base, oracle) version-comparison cases**
- Groups represented: 345
- Laws (tyviseadus/muutmisseadus pairs): 3009
- Decrees (maarus/muutmismaarus/juurakt pairs): 0

## Selection Criteria

1. Schema filter.
   Laws only (tyviseadus/muutmisseadus).
2. Body content: each terviktekst must contain `<peatykk>` or `<paragrahv>` elements and
   be at least 500 bytes.
3. 2+ structured tervikteksts per group.
4. Pair selection: every consecutive consolidated-version pair by `kehtivuseAlgus`.

This corpus is for historical adjacent-version replay review. The public
current divergence surface should use `current_replayable_corpus.csv`.

## Exclusion Stats

| Reason | Count |
|--------|-------|
| schema_not_allowed | 18136 |
| fewer_than_2_tervikteksts | 4345 |

## Distribution by Schema

| Schema | Count |
|--------|-------|
| tyviseadus | 3009 |

## Distribution by Amendment Count

| Bucket | Count |
|--------|-------|
| 4-10 | 8 |
| 11-50 | 45 |
| 51+ | 2956 |

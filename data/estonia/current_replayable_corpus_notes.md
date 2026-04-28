# EE Current Replayable Corpus Notes

**Date**: 2026-04-28
**Archive**: data/ee_riigiteataja.farchive

## Summary

- Source: Riigi Teataja Farchive — 22826 unique terviktekstiGrupiID groups
- **Current replayable corpus: 2203 latest-version comparison cases**
- Laws (tyviseadus/muutmisseadus pairs): 345
- Decrees (maarus/muutmismaarus/juurakt pairs): 1858

## Selection Criteria

1. Schema filter.
   Laws + decrees included.
2. Amendment history: group must have at least one `<muutmismarge>`.
3. Body content: each terviktekst must contain `<peatykk>` or `<paragrahv>` elements and
   be at least 500 bytes.
4. 2+ structured tervikteksts per group.
5. Pair selection: penultimate structured consolidated version as base, latest
   structured consolidated version as current oracle, by `kehtivuseAlgus`.

This corpus is the public Estonia divergence browser input. It intentionally
does not expose every historical adjacent version pair.

## Exclusion Stats

| Reason | Count |
|--------|-------|
| no_amendments | 14113 |
| fewer_than_2_tervikteksts | 6509 |
| schema_not_allowed | 1 |

## Distribution by Schema

| Schema | Count |
|--------|-------|
| maarus | 1858 |
| tyviseadus | 345 |

## Distribution by Amendment Count

| Bucket | Count |
|--------|-------|
| 4-10 | 150 |
| 11-50 | 929 |
| 51+ | 1124 |

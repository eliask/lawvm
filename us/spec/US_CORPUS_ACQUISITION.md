# US Code edition corpus acquisition

Broadens the U.S. Code annual-edition oracle corpus in `data/us_federal.farchive`
(gitignored) to feed the dry-run bench and the upcoming non-positive-title replay
extension. Pure data acquisition: no lowering/dry-run code touched.

Source: keyless govinfo `/content/pkg/` annual editions, content URL
`https://www.govinfo.gov/content/pkg/USCODE-{year}-title{N}/html/USCODE-{year}-title{N}.htm`,
imported via `python -m lawvm.us_federal.import_usc --skip-existing` (SHA-256
idempotent; a second pass skipped all 63 staged files).

## Editions acquired

All probed title-years returned HTTP 200 — **no 404s**. govinfo serves the full
2014–2024 ladder for every title below. Sizes are the `.htm` download size.

### Deeper year ladder — positive-law bench titles

These titles already had recent editions; the 2014/2016/2018/2020 editions below
were newly added to open richer multi-year adjacent windows. (`*` = already
present in the farchive, skipped on import.)

| Title | Newly acquired editions | htm sizes |
| --- | --- | --- |
| 11 Bankruptcy   | 2014, 2016                         | 2.7M, 2.7M (2018*/2020*/2022*/2023*/2024* present) |
| 18 Crimes       | 2014, 2016, 2018                   | 7.6M, 7.8M, 8.0M (2020*/2022*/2023*/2024* present) |
| 28 Judiciary    | 2014, 2016, 2018, 2020             | 5.4M, 5.5M, 5.5M, 5.7M (2022*/2023*/2024* present) |
| 35 Patents      | 2014, 2016                         | 1.3M, 1.3M (2018*/2020*/2022*/2023*/2024* present) |
| 38 Veterans     | 2014, 2016, 2018, 2020             | 9.0M, 9.4M, 11M, 12M (2022*/2023*/2024* present) |

### New positive-law titles (full 2014–2024 ladder)

High-amendment positive titles added in full to deepen the bench.

| Title | Editions acquired | htm sizes |
| --- | --- | --- |
| 10 Armed Forces      | 2014, 2016, 2018, 2020, 2022, 2023, 2024 | 24M, 26M, 28M, 32M, 33M, 34M, 35M |
| 31 Money & Finance   | 2014, 2016, 2018, 2020, 2022, 2023, 2024 | 4.5M, 4.5M, 4.6M, 5.0M, 5.1M, 5.2M, 5.2M |
| 49 Transportation    | 2014, 2016, 2018, 2020, 2022, 2023, 2024 | 12M, 13M, 14M, 14M, 15M, 15M, 16M |

### Non-positive-law titles (for the replay extension)

Recent editions for the act→USC mapping replay extension. Title 26 zip is large
(~500MB) but the `.htm` itself is ~35M and fetched fine.

| Title | Editions acquired | htm sizes |
| --- | --- | --- |
| 7 Agriculture          | 2020, 2022, 2023, 2024 | 18M, 19M, 19M, 19M |
| 20 Education           | 2020, 2022, 2023, 2024 | 15M, 15M, 15M, 15M |
| 26 Internal Revenue    | 2020, 2022, 2023, 2024 | 33M, 35M, 35M, 35M |
| 42 Public Health       | 2020, 2022, 2023, 2024 | 69M, 74M, 75M, 75M |

Farchive USC oracle inventory after import (73 editions total):

```
T7:  2020 2022 2023 2024
T10: 2014 2016 2018 2020 2022 2023 2024
T11: 2014 2016 2018 2020 2022 2023 2024
T15: 2023
T18: 2014 2016 2018 2020 2022 2023 2024
T20: 2020 2022 2023 2024
T26: 2020 2022 2023 2024
T28: 2014 2016 2018 2020 2022 2023 2024
T31: 2014 2016 2018 2020 2022 2023 2024
T35: 2014 2016 2018 2020 2022 2023 2024
T38: 2014 2016 2018 2020 2022 2023 2024
T42: 2020 2022 2023 2024
T49: 2014 2016 2018 2020 2022 2023 2024
```

## New bench windows

Witness deltas were computed per adjacent-edition window via
`derive_window_law_locators` before adding rows; only non-empty deltas are
`include=true`. Empty deltas are kept as honest `include=false` records (the
after edition credits no new source-credit public law).

New `include=true` windows (window_law_count = witness-delta size at authoring):

| Title | Windows added (delta size) |
| --- | --- |
| 11 | 2016→2018 (1) |
| 18 | 2014→2016 (15), 2016→2018 (20), 2018→2020 (15) |
| 28 | 2014→2016 (4), 2016→2018 (5), 2018→2020 (8), 2020→2022 (9) |
| 35 | 2014→2016 (1) |
| 38 | 2014→2016 (18), 2016→2018 (21), 2018→2020 (21), 2020→2022 (27) |
| 10 | 2014→2016 (6), 2016→2018 (4), 2018→2020 (4), 2020→2022 (7), 2022→2023 (1), 2023→2024 (1) |
| 31 | 2014→2016 (8), 2016→2018 (12), 2018→2020 (12), 2020→2022 (8), 2023→2024 (5) |
| 49 | 2014→2016 (18), 2016→2018 (15), 2018→2020 (7), 2020→2022 (9), 2022→2023 (2), 2023→2024 (6) |
| 7  | 2020→2022 (7), 2022→2023 (2), 2023→2024 (6) |
| 20 | 2020→2022 (9), 2022→2023 (2), 2023→2024 (6) |
| 26 | 2020→2022 (10), 2022→2023 (2), 2023→2024 (7) |
| 42 | 2020→2022 (42), 2022→2023 (5), 2023→2024 (29) |

New `include=false` (empty-delta) records: 11 2014→2016, 35 2016→2018,
31 2022→2023. (Existing empties retained: 11 2022→2023, 35 2022→2023.)

Corpus now: 60 rows (55 include=true, 5 include=false), up from 15 rows.

## Bench aggregate

`uv run python -m lawvm.us_federal.bench`:

```
AGGREGATE  windows=55 (skipped 5)  witness-anchored coverage=361/8514 = 0.0424
  disposition breakdown: {'agreement': 361, 'lawvm_wrong': 1666,
    'oracle_suspect': 107, 'missing_source': 6719, 'sunset_reversion': 29}
  replay_authorized: False (dry-run gate)
```

The coverage fraction is honest against the much larger denominator now that the
corpus spans 55 evaluated windows across 12 titles (was 13 windows / 5 titles).
The absolute agreement count grew with the broader corpus; the fraction reflects
the bigger, harder denominator (many non-positive-title sections the dry-run
kernel does not yet lower — the replay-extension target).

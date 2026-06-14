# US federal dry-run eval state

Status: descriptive snapshot of the CURRENT kernel measured over the committed
bench corpus. This is a **witness-anchored dry-run** number, not a replay claim:
every window runs with `replay_authorized=False`. The denominator is the
oracle's changed-section count — a fact of the two editions — per the monotone
north-star discipline. "Covered" means a section materialized in agreement with
the oracle after-text; it NEVER folds in a `sunset_reversion`, an
`oracle_suspect`, or a `missing_source` (those are reported as distinct typed
partitions below).

Reproduce: `python -m lawvm.us_federal.bench` (table) or `--json` (machine form);
also `python scripts/us_bench.py`. Requires the canonical `us_federal.farchive`
(via `LAWVM_CANONICAL_DATA_ROOT`).

## Bench corpus

`us/bench/us_bench_corpus.csv` — adjacent USC-edition windows. The window's
amending laws are NOT stored in the corpus; they are **derived at run time** from
the witness delta (public laws whose `source-credit` first appears in the after
edition), so the corpus cannot drift from the editions. Two empty-delta windows
are recorded (`include=false`) and skipped, never run as a misleading zero.

| window | derived window laws | prior editions (F2 channel a) |
|---|---|---|
| title11:2018->2020 | 8 (116th: SBRA/CARES) | — |
| title11:2020->2022 | 4 (117th) | 2018 |
| title11:2022->2023 | 0 — **empty, skipped** | — |
| title11:2023->2024 | 1 (PL 118-42) | 2018, 2022 |
| title18:2020->2022 | 20 (117th crimes) | — |
| title18:2022->2023 | 3 (118th) | — |
| title18:2023->2024 | 9 (118th) | — |
| title35:2018->2020 | 1 (116th patents) | — |
| title35:2020->2022 | 3 (117th) | — |
| title35:2022->2023 | 0 — **empty, skipped** | — |
| title35:2023->2024 | 1 (PL 118-151) | — |
| title28:2022->2023 | 1 (118th judiciary) | — |
| title28:2023->2024 | 4 (118th) | — |
| title38:2022->2023 | 5 (118th veterans) | — |
| title38:2023->2024 | 10 (118th) | — |

Editions acquired into the canonical `us_federal.farchive` for this corpus
(keyless govinfo `/content/pkg/USCODE-{year}-title{N}/html/...`, raw htm kept OUT
of git): Title 18 (2020), Title 35 (2018, 2020), Title 28 (2022, 2023, 2024),
Title 38 (2022, 2023, 2024). Title 11 (2018/2020/2022/2023/2024) and Title
18/35 (2022/2023/2024) were already present. No title-year 404'd. Edition sizes
range ~1.3 MB (Title 35) to ~12.7 MB (Title 38 2024).

## Per-window result (current kernel)

Columns: oracle Δ = oracle-changed sections (denominator); agree = sections
materialized in agreement (numerator); cov = witness-anchored coverage; then the
typed residual partitions; refused = section-granularity refusals (off-title /
sub-section structural / section-not-in-before — these are correct refusals, NOT
coverage gaps).

| window | oracle Δ | agree | cov | lawvm_wrong | oracle_suspect | missing_src | sunset | refused |
|---|---|---|---|---|---|---|---|---|
| title11:2018->2020 | 40 | 1 | 0.025 | 12 | 1 | 27 | 1 | 833 |
| title11:2020->2022 | 19 | 1 | 0.053 | 2 | 0 | 8 | 8 | 19 |
| title11:2023->2024 | 3 | 0 | 0.000 | 0 | 1 | 0 | 2 | 39 |
| title18:2020->2022 | 35 | 3 | 0.086 | 12 | 0 | 23 | 0 | 1578 |
| title18:2022->2023 | 4 | 0 | 0.000 | 2 | 0 | 2 | 0 | 271 |
| title18:2023->2024 | 19 | 0 | 0.000 | 4 | 0 | 16 | 0 | 329 |
| title35:2018->2020 | 2 | 0 | 0.000 | 0 | 0 | 2 | 0 | 440 |
| title35:2020->2022 | 4 | 0 | 0.000 | 0 | 0 | 4 | 0 | 665 |
| title35:2023->2024 | 2 | 0 | 0.000 | 0 | 0 | 2 | 0 | 0 |
| title28:2022->2023 | 1 | 0 | 0.000 | 1 | 0 | 1 | 0 | 216 |
| title28:2023->2024 | 7 | 0 | 0.000 | 3 | 0 | 4 | 0 | 0 |
| title38:2022->2023 | 21 | 2 | 0.095 | 3 | 0 | 17 | 0 | 216 |
| title38:2023->2024 | 74 | 16 | 0.216 | 9 | 0 | 49 | 0 | 275 |

Skipped (empty witness delta, recorded not run): title11:2022->2023,
title35:2022->2023.

## Aggregate

- **Witness-anchored coverage = 23 / 231 = 0.0996** across 13 evaluated windows
  (2 skipped).
- Disposition breakdown (each is a DISTINCT partition; only `agreement` is
  coverage):
  - `agreement`: **23** (the covered numerator)
  - `lawvm_wrong`: 48 (genuine lowering incompleteness — multiply-amended
    sections with an un-lowered sibling op, heading-inlining, footnote digits)
  - `oracle_suspect`: 2 (OLRC editorial pathology on the oracle side; never
    repaired to the oracle)
  - `missing_source`: 155 (oracle changed a section the kernel never claimed —
    the honest lowering gap, dominated by sub-section structural redesignations
    refused at section granularity, and ops not yet lowered)
  - `sunset_reversion`: 11 (a temporal expiry the F2 layer EXPLAINS but does not
    materialize from source — e.g. the SBRA debt-limit reverting on the 2020->2022
    Title 11 window)
- Refusals total: 4881 (correct section-granularity refusals — off-title ops in
  omnibus laws, sub-section structural targets, sections absent from the before
  edition; NOT coverage gaps).
- `replay_authorized: False` for every window (dry-run gate).

## Honest read

This measures the **current kernel** — a witness comparison of materialized
section text against the official USC annual edition, not actual replay. The
single most-informative number is **0.0996 aggregate coverage**, dominated by
`missing_source` (155) and `lawvm_wrong` (48): the kernel today lowers a minority
of the section-level textual amendments and refuses (correctly) the large mass of
sub-section structural redesignations rather than wrong-materializing them. The
high `refused` counts are a feature, not a gap — an omnibus public law carries
hundreds of ops targeting other titles or sub-section addresses, and each is
typed-refused, never hijacked into Title-N.

Where coverage IS non-trivial it is concentrated in textual-amendment-heavy
windows: title38:2023->2024 (16/74 = 0.216) and title18:2020->2022 (3/35) carry
most of the agreements. Windows dominated by structural redesignation
(title35:*, title28:*) sit at zero — honestly, because those amendments are not
section-text representable and the kernel refuses them.

**Expect these numbers to rise.** A sibling agent is raising coverage via
sub-section materialization — exactly the mechanism that currently routes most of
the `missing_source` mass (sub-section REPLACE/INSERT redesignations) into
refusals. As that lands, `missing_source` should fall and `agreement` should
rise on the structural-redesignation-heavy windows. This report is a snapshot of
the kernel BEFORE that work; the harness (`lawvm.us_federal.bench`) is the
instrument to re-measure after.

Crucially: `sunset_reversion` (11) and `oracle_suspect` (2) are NOT coverage and
are NOT defects to "fix" by repairing to the oracle. They are correctly-typed
non-agreements — a temporal expiry the source amendments cannot produce, and an
OLRC editorial artifact on the oracle side — kept visible, never folded into the
covered numerator.

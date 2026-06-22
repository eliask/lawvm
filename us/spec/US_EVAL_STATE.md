# US federal dry-run eval state

> **⚠ STALE:** The per-window table below predates several amendatory lowering
> improvements (relative-prose targeting, sub-section materialization, editorial
> projection comma-anchor, non-positive-title routing, deferred-op
> reclassification). On a full-corpus scan (2026-06-22, 248 windows), the
> aggregate coverage is 2,395/45,735 = 0.0524. Individual windows have
> improved dramatically: e.g., title11:2018→2020 went from 1/40 = 0.025 to
> 16/40 = 0.400. The table should be refreshed by a future `python -m
> lawvm.us_federal.bench` run.

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
edition), so the corpus cannot drift from the editions. Empty-delta windows are
recorded (`include=false`) and skipped, never run as a misleading zero.

The corpus spans both **positive-law** titles (11 bankruptcy, 18 crimes, 28
judiciary, 35 patents, 38 veterans, 10 armed forces, 31 money and finance, 49
transportation — the title IS enacted text, the amendment cites the Code
directly) and **non-positive-law** titles (7 agriculture, 20 education, 26
internal revenue, 42 public health — an OLRC editorial arrangement of free-standing
Acts; the amendment names the originating Act and the codified target comes from
the act-section→USC route, see `US_NONPOSITIVE_TITLES.md`). Editions are acquired
keyless from govinfo (`/content/pkg/USCODE-{year}-title{N}/html/...`, raw htm kept
OUT of git).

## Per-window result (current kernel)

Columns: oracle Δ = oracle-changed sections (denominator); agree = sections
materialized in agreement (numerator); cov = witness-anchored coverage; then the
typed residual partitions; refused = section-granularity refusals (off-title /
sub-section structural / section-not-in-before — these are correct refusals, NOT
coverage gaps).

| window | oracle Δ | agree | cov | lawvm_wrong | oracle_suspect | missing_src | sunset | refused |
|---|---|---|---|---|---|---|---|---|
| title11:2016->2018 | 17 | 0 | 0.000 | 0 | 0 | 17 | 0 | 3 |
| title11:2018->2020 | 40 | 1 | 0.025 | 14 | 3 | 24 | 0 | 2220 |
| title11:2020->2022 | 19 | 1 | 0.053 | 6 | 1 | 3 | 8 | 25 |
| title11:2023->2024 | 3 | 0 | 0.000 | 0 | 1 | 0 | 2 | 101 |
| title18:2014->2016 | 58 | 2 | 0.034 | 24 | 4 | 31 | 0 | 1914 |
| title18:2016->2018 | 94 | 9 | 0.096 | 36 | 3 | 56 | 0 | 4237 |
| title18:2018->2020 | 27 | 2 | 0.074 | 10 | 0 | 20 | 0 | 4040 |
| title18:2020->2022 | 35 | 2 | 0.057 | 22 | 2 | 15 | 0 | 4195 |
| title18:2022->2023 | 4 | 0 | 0.000 | 3 | 0 | 1 | 0 | 819 |
| title18:2023->2024 | 19 | 0 | 0.000 | 10 | 0 | 10 | 0 | 826 |
| title35:2014->2016 | 10 | 0 | 0.000 | 1 | 0 | 9 | 0 | 8 |
| title35:2018->2020 | 2 | 0 | 0.000 | 1 | 0 | 1 | 0 | 1161 |
| title35:2020->2022 | 4 | 1 | 0.250 | 1 | 0 | 2 | 0 | 1798 |
| title35:2023->2024 | 2 | 0 | 0.000 | 0 | 0 | 2 | 0 | 0 |
| title28:2014->2016 | 9 | 0 | 0.000 | 3 | 0 | 6 | 0 | 182 |
| title28:2016->2018 | 9 | 1 | 0.111 | 3 | 0 | 5 | 0 | 903 |
| title28:2018->2020 | 17 | 2 | 0.118 | 6 | 2 | 7 | 0 | 2184 |
| title28:2020->2022 | 10 | 1 | 0.100 | 4 | 1 | 4 | 0 | 2770 |
| title28:2022->2023 | 1 | 0 | 0.000 | 2 | 0 | 1 | 0 | 697 |
| title28:2023->2024 | 7 | 0 | 0.000 | 3 | 1 | 4 | 0 | 0 |
| title38:2014->2016 | 124 | 3 | 0.024 | 32 | 5 | 88 | 0 | 2032 |
| title38:2016->2018 | 164 | 7 | 0.043 | 39 | 0 | 121 | 0 | 2679 |
| title38:2018->2020 | 190 | 19 | 0.100 | 86 | 5 | 105 | 0 | 3083 |
| title38:2020->2022 | 205 | 29 | 0.141 | 80 | 5 | 99 | 0 | 4150 |
| title38:2022->2023 | 21 | 3 | 0.143 | 6 | 1 | 12 | 0 | 697 |
| title38:2023->2024 | 74 | 16 | 0.216 | 26 | 1 | 31 | 0 | 690 |
| title10:2014->2016 | 498 | 30 | 0.060 | 264 | 6 | 297 | 0 | 1328 |
| title10:2016->2018 | 2037 | 24 | 0.012 | 252 | 3 | 1801 | 0 | 778 |
| title10:2018->2020 | 1012 | 54 | 0.053 | 364 | 6 | 664 | 1 | 2364 |
| title10:2020->2022 | 787 | 47 | 0.060 | 252 | 14 | 526 | 1 | 3447 |
| title10:2022->2023 | 360 | 14 | 0.039 | 92 | 7 | 259 | 0 | 369 |
| title10:2023->2024 | 314 | 26 | 0.083 | 92 | 7 | 200 | 0 | 316 |
| title31:2014->2016 | 23 | 1 | 0.043 | 2 | 0 | 20 | 0 | 2256 |
| title31:2016->2018 | 16 | 2 | 0.125 | 14 | 0 | 5 | 0 | 4018 |
| title31:2018->2020 | 50 | 3 | 0.060 | 25 | 2 | 24 | 0 | 4433 |
| title31:2020->2022 | 21 | 9 | 0.429 | 10 | 1 | 5 | 0 | 4192 |
| title31:2023->2024 | 18 | 0 | 0.000 | 11 | 0 | 7 | 0 | 551 |
| title49:2014->2016 | 241 | 15 | 0.062 | 97 | 17 | 119 | 0 | 1896 |
| title49:2016->2018 | 219 | 9 | 0.041 | 68 | 7 | 138 | 0 | 3261 |
| title49:2018->2020 | 53 | 7 | 0.132 | 22 | 1 | 25 | 1 | 4220 |
| title49:2020->2022 | 125 | 16 | 0.128 | 48 | 9 | 53 | 0 | 4903 |
| title49:2022->2023 | 18 | 11 | 0.611 | 5 | 0 | 2 | 0 | 61 |
| title49:2023->2024 | 171 | 18 | 0.105 | 86 | 9 | 59 | 0 | 73 |
| title7:2020->2022 | 46 | 1 | 0.022 | 36 | 0 | 11 | 0 | 3144 |
| title7:2022->2023 | 20 | 6 | 0.300 | 11 | 0 | 3 | 0 | 54 |
| title7:2023->2024 | 26 | 7 | 0.269 | 1 | 0 | 18 | 0 | 353 |
| title20:2020->2022 | 39 | 0 | 0.000 | 30 | 0 | 21 | 0 | 3128 |
| title20:2022->2023 | 4 | 0 | 0.000 | 3 | 0 | 2 | 0 | 697 |
| title20:2023->2024 | 45 | 0 | 0.000 | 10 | 0 | 32 | 4 | 689 |
| title26:2020->2022 | 160 | 8 | 0.050 | 89 | 0 | 79 | 0 | 4921 |
| title26:2022->2023 | 29 | 0 | 0.000 | 2 | 0 | 27 | 0 | 90 |
| title26:2023->2024 | 26 | 0 | 0.000 | 8 | 0 | 18 | 0 | 532 |
| title42:2020->2022 | 768 | 20 | 0.026 | 209 | 0 | 562 | 1 | 5520 |
| title42:2022->2023 | 71 | 1 | 0.014 | 14 | 0 | 56 | 3 | 825 |
| title42:2023->2024 | 152 | 5 | 0.033 | 57 | 1 | 109 | 1 | 911 |

Skipped (empty witness delta, recorded not run): title11:2014->2016,
title11:2022->2023, title35:2016->2018, title35:2022->2023, title31:2022->2023.

## Aggregate

- **Witness-anchored coverage = 433 / 8514 = 0.0509** across 55 evaluated windows
  (5 skipped). All 433 are exact `materialized==oracle` matches inside the oracle
  changed set; no agreement is forced.

### Per-title-class split (positive-law vs non-positive-law)

| class | windows | agree | oracle Δ | coverage |
|---|---|---:|---:|---:|
| positive-law (11/18/28/35/38/10/31/49) | 43 | 385 | 7128 | 0.0540 |
| non-positive-law (7/20/26/42) | 12 | 48 | 1386 | 0.0346 |
| **total** | **55** | **433** | **8514** | **0.0509** |

The non-positive titles are reached through the **act-section→USC route** (the
amendment names a free-standing Act; the codified target comes from the govinfo
USLM `(N U.S.C. M)` parenthetical + structural href, routed through
`nonpositive.resolve_nonpositive_target`; see `US_NONPOSITIVE_TITLES.md` §6). T42
carries the bulk (26 of 48); T7 follows (14); T26 has 8; T20 has 0 (its windows
are dominated by structural redesignations whose sibling ops are not yet lowered).

- Disposition breakdown (each is a DISTINCT partition; only `agreement` is
  coverage):
  - `agreement`: **433** (the covered numerator; all exact materialized==oracle
    matches inside the oracle changed set)
  - `lawvm_wrong`: 2592 (genuine lowering incompleteness — multiply-amended
    sections with an un-lowered sibling op, heading-inlining, footnote digits)
  - `oracle_suspect`: 125 (OLRC editorial pathology on the oracle side; never
    repaired to the oracle)
  - `missing_source`: 5816 (oracle changed a section the kernel never claimed —
    the honest lowering gap; dominated by un-lowered structural ops and, for the
    non-positive titles, uncodified act-section targets held out at the boundary)
  - `sunset_reversion`: 22 (a temporal expiry the F2 layer EXPLAINS but does not
    materialize from source)
- Refusals total: 100744 (correct section-granularity refusals — off-title ops in
  omnibus laws, sub-section structural targets, sections absent from the before
  edition, and strike-of-absent-node no-ops; NOT coverage gaps).
- `replay_authorized: False` for every window (dry-run gate).

## Levers landed this pass (all in `amendatory.py` / `dry_run.py`)

**A. Relative-prose + nested-instruction-list target threading (THE coverage
lever).** The dominant `missing_source` mass was sections the lowering never even
TARGETED — an instruction unit "(1) in subsection (a), by inserting …" or "(B) in
section 3675(b)(3), by striking …" carries no `<ref>` and no "of title N" prose,
so the old surface (which only threaded the section-level ref) produced no op.
Now `_iter_instruction_units` threads the address resolved by the nearest
ENCLOSING instruction into each leaf (`inherited_address`); `parse_relative_usc_target`
resolves a bare "section X(...) of such title" under the inherited title (never
invents a title; a cross-reference "section 116 of title 18" in inserted text is
NOT matched); and `_refine_with_leading_subunit_anchor` appends a leading "in
subsection (a)" anchor so sibling ops do not collapse onto the same section
address and double-apply (fixed the §11:104 `1182(1),1182(1),` bug).

**B. Structural-op lowering + sub-section materialization.** Forms that were
typed `us_amendatory_unlowered` findings now lower to typed ops AND materialize at
sub-section granularity where faithfully representable: **strike-subsection** ->
sub-section REPEAL (a FUTURE-effective strike is left to the temporal layer; a
strike of a node absent from the before edition is a typed REFUSAL no-op, never an
over-broad deletion); **range redesignation** -> one RENUMBER per member (relabels
only each node's leading enumerator; non-numeric ranges stay typed findings);
**insert-node-after** -> an anchored INSERT splicing the payload after the anchor
node. On-title unlowered findings fell 96 -> 73.

**C. Comma-anchor editorial projection (generalized F1).** The OLRC adds a
courtesy space after a `,` or `)` insert-after anchor that the enacted quotedText
literal does not carry (faithful `Tacoma,Mount Vernon,` vs published
`Tacoma, Mount Vernon,`); `_norm_editorial` folds it symmetrically, re-typing
several rows from `lawvm_wrong` to `oracle_suspect` (oracle editorial, never
repaired, never coverage).

**D. Non-positive-law title routing through the act-section→USC resolver.**
`amendatory._resolve_target` now routes a target that lands on a non-positive
title (7/20/26/42, …) through `nonpositive.resolve_nonpositive_target`. Codified
act-section targets resolve to a USC address (`nonpositive_<status>`) and feed the
existing payload lowering + section materialization unchanged; an UNCODIFIED
`note`-only / unmapped target is **held out at the lowering boundary** (resolves
`unresolved`, never guessed onto a codified section), and the IRC single-letter
subsection `(l)` is typed by nesting position. Only the unit's own phrase/href are
consulted (no raw-text paren), so a stray `(N U.S.C. M)` cross-citation cannot
hijack the target. Net bench effect: **agreements 432→433** (a new exact,
hand-verified non-positive agreement at §42:4016), **`lawvm_wrong` 2623→2592** (the
note-only holdout demotes ~31 previously mis-resolved non-positive claims off the
false-claim partition), and **every positive-law title window byte-identical** in
the agreement column. See `US_NONPOSITIVE_TITLES.md` §6.

## Honest read

This measures the **current kernel** — a witness comparison of materialized
section text against the official USC annual edition, not actual replay. The
single most-informative number is **0.0509 aggregate coverage** (433/8514),
dominated by `missing_source` (5816) and `lawvm_wrong` (2592). The corpus now
spans positive-law AND non-positive-law titles; coverage is real but low because
most oracle-changed sections are multiply-amended with at least one still-un-lowered
sibling op (landing as honest `lawvm_wrong` residuals) or are never targeted at all
(the `missing_source` mass, dominated by un-lowered structural ops). No agreement
is forced. The high `refused` counts are a feature — an omnibus public law carries
thousands of ops targeting other titles or absent sub-section addresses, each
typed-refused.

Where coverage IS non-trivial it is concentrated in textual-amendment-heavy
windows: title49:2022->2023 (11/18 = 0.611), title31:2020->2022 (9/21 = 0.429),
title38:2023->2024 (16/74 = 0.216), and the T7/T42 non-positive textual windows
(T7:2022->2023 6/20, T7:2023->2024 7/26). Windows dominated by structural
redesignation (title35:*, title28:*, title20:*) are still near zero — honestly,
because those sections are multiply-amended and the binding constraint remains the
still-un-lowered sibling ops, not the structural materialization (in place and
correct).

**Next coverage lever (honest).** The remaining 73 on-title unlowered forms are
dominated by punctuation strike_insert (16: "striking the period at the end and
inserting '; and'") and non-numeric / multi-unit redesignations (35). Lowering the
punctuation strike_insert form (the strike anchor is descriptive prose, not a
quotedText) is the next AGREEMENT-yielding work: several near-miss sections
(ratio > 0.99) are blocked solely by one such sibling op or by the F3 footnote
digit (deliberately not forced).

Crucially: `sunset_reversion` (22) and `oracle_suspect` (125) are NOT coverage and
are NOT defects to "fix" by repairing to the oracle. They are correctly-typed
non-agreements — a temporal expiry the source amendments cannot produce, and OLRC
editorial artifacts on the oracle side — kept visible, never folded into the
covered numerator. So too the non-positive **uncodified** act-section targets
(note-only / appropriations / Stat.-cite): held out, never guessed onto a section.

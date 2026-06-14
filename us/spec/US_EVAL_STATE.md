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
| title11:2018->2020 | 40 | 1 | 0.025 | 14 | 3 | 24 | 0 | 2306 |
| title11:2020->2022 | 19 | 1 | 0.053 | 6 | 1 | 3 | 8 | 26 |
| title11:2023->2024 | 3 | 0 | 0.000 | 0 | 1 | 0 | 2 | 102 |
| title18:2020->2022 | 35 | 2 | 0.057 | 22 | 2 | 15 | 0 | 4300 |
| title18:2022->2023 | 4 | 0 | 0.000 | 3 | 0 | 1 | 0 | 839 |
| title18:2023->2024 | 19 | 0 | 0.000 | 10 | 0 | 10 | 0 | 849 |
| title35:2018->2020 | 2 | 0 | 0.000 | 1 | 0 | 1 | 0 | 1226 |
| title35:2020->2022 | 4 | 1 | 0.250 | 1 | 0 | 2 | 0 | 1827 |
| title35:2023->2024 | 2 | 0 | 0.000 | 0 | 0 | 2 | 0 | 0 |
| title28:2022->2023 | 1 | 0 | 0.000 | 2 | 0 | 1 | 0 | 717 |
| title28:2023->2024 | 7 | 0 | 0.000 | 3 | 1 | 4 | 0 | 0 |
| title38:2022->2023 | 21 | 3 | 0.143 | 6 | 1 | 12 | 0 | 717 |
| title38:2023->2024 | 74 | 16 | 0.216 | 26 | 1 | 31 | 0 | 707 |

Skipped (empty witness delta, recorded not run): title11:2022->2023,
title35:2022->2023.

## Aggregate

- **Witness-anchored coverage = 24 / 231 = 0.1039** across 13 evaluated windows
  (2 skipped). Up from 23/231: the target-threading lever (below) claimed §35:5
  (two composed strike-and-inserts) into a genuine, exact, hand-verified
  agreement; no agreement was forced.
- Disposition breakdown (each is a DISTINCT partition; only `agreement` is
  coverage):
  - `agreement`: **24** (the covered numerator; all 24 are exact
    materialized==oracle matches inside the oracle changed set — verified)
  - `lawvm_wrong`: 94 (genuine lowering incompleteness — multiply-amended
    sections with an un-lowered sibling op, heading-inlining, footnote digits.
    Rose from 48 because target threading now CLAIMS many more sections whose
    sibling ops are still un-lowered; each is an honest typed residual, not a
    forced agreement.)
  - `oracle_suspect`: 10 (OLRC editorial pathology on the oracle side — the
    comma-anchor courtesy-space generalization of F1 re-typed several rows here;
    never repaired to the oracle)
  - `missing_source`: 106 (oracle changed a section the kernel never claimed —
    the honest lowering gap; down from 155 as target threading lowered ops for
    sections previously never targeted)
  - `sunset_reversion`: 10 (a temporal expiry the F2 layer EXPLAINS but does not
    materialize from source — e.g. the SBRA debt-limit reverting on the 2020->2022
    Title 11 window)
- Refusals total: 13616 (correct section-granularity refusals — off-title ops in
  omnibus laws, sub-section structural targets, sections absent from the before
  edition, and strike-of-absent-node no-ops; NOT coverage gaps. Rose because the
  threading resolves far more ops, most off the proof title in these omnibus laws
  and typed-refused, never hijacked into Title-N.)
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

## Honest read

This measures the **current kernel** — a witness comparison of materialized
section text against the official USC annual edition, not actual replay. The
single most-informative number is **0.1039 aggregate coverage**, dominated by
`missing_source` (106) and `lawvm_wrong` (94). The target-threading diagnosis was
right (the binding constraint was the lowering layer never TARGETING ~136 of the
oracle-changed sections), and it lifted coverage to 24 with §35:5, but most
newly-claimed sections are multiply-amended with at least one still-un-lowered
sibling op, so they land as honest `lawvm_wrong` residuals rather than agreements.
The high `refused` counts are a feature — an omnibus public law carries thousands
of ops targeting other titles or absent sub-section addresses, each typed-refused.

Where coverage IS non-trivial it is concentrated in textual-amendment-heavy
windows: title38:2023->2024 (16/74 = 0.216) and title38:2022->2023 (3/21) carry
most of the agreements. Windows dominated by structural redesignation
(title35:*, title28:*) are still near zero — honestly, because those sections are
multiply-amended and the binding constraint remains the still-un-lowered sibling
ops, not the structural materialization (which is in place and correct).

**Next coverage lever (honest).** The remaining 73 on-title unlowered forms are
dominated by punctuation strike_insert (16: "striking the period at the end and
inserting '; and'") and non-numeric / multi-unit redesignations (35). Lowering the
punctuation strike_insert form (the strike anchor is descriptive prose, not a
quotedText) is the next AGREEMENT-yielding work: several near-miss sections
(ratio > 0.99) are blocked solely by one such sibling op or by the F3 footnote
digit (deliberately not forced).

Crucially: `sunset_reversion` (10) and `oracle_suspect` (10) are NOT coverage and
are NOT defects to "fix" by repairing to the oracle. They are correctly-typed
non-agreements — a temporal expiry the source amendments cannot produce, and OLRC
editorial artifacts on the oracle side — kept visible, never folded into the
covered numerator.

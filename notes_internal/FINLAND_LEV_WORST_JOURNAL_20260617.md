# Finland Levenshtein Worst-Case Journal (2026-06-17)

Original source run: `data/bench_runs/20260616T2225_run_20260616T2225.csv`

Current refresh run: `data/bench_runs/20260617T1132_run_20260617T1132.csv`
(previous: `data/bench_runs/20260617T1129_run_20260617T1129.csv` partial,
`data/bench_runs/20260617T1106_run_20260617T1106.csv`,
`data/bench_runs/20260617T1058_run_20260617T1058.csv`)

**Discrete-fix gate:** after any candidate replay fix, run full bench and compare
aggregate + per-statute rows, e.g.
`time uv run lawvm bench --parallel 16` (tail summary for struct/lev).

Current top Levenshtein gaps from the refresh run:

- `1991/1208`: lev `0.598139`, structural `0.920000` — province-table merge
  landed; residual attachment/PDF frontier (`2001/995`+); journaled below.
- `2017/320`: diff **90.56%** (was `88.07%`) — migration ledger lookup +
  `part:2a`/`iia` structural alias landed; same-wave `duplicate_label` and
  omission shells remain; journaled below.
- `1868/31-000`: lev `0.692415`, structural `0.913793` — §85 overlay + §83
  letter-list fixes landed (`0.606019` → `0.692415`); residuals oracle-stale /
  source-incomplete; journaled below.
- `1992/1535`: lev `0.727505`, structural `0.908213` — comparison/adjudication
  band; journaled below.
- `1929/234`: lev `0.821830`, structural `0.887417` — part-move boundary +
  part-insert uncovered-body fix landed; residual editorial/extra topology;
  journaled below.

Purpose: track high-Levenshtein-gap Finnish statutes, root-cause classification,
and whether the case is actionable or deferred. Entries here are working notes:
do not treat a statute as fixed merely because it is listed.

## Universal vs quirks-mode classification

When triaging fixes, classify by **where** the behavior lives and **whether strict
mode should allow it without a named witness**:

| Class | Meaning | Strict mode | Examples in this journal |
|-------|---------|-------------|--------------------------|
| **Universal invariant** | Source or typed op already owns the mutation; fixing a boundary violation | Should pass strict | `1987/322` repeal-placeholder descendant barrier (core PIT); `2001/1234` item-scoped op must not emit whole-section snapshots (§1.3 granularity) |
| **Universal compile/elaboration rule** | Deterministic lowering from explicit source text; no inference beyond safe anchors | Should pass strict when anchors are unique | `1979/319` VTS mixed genitive-tail parse; `_rewrite_partial_whole_section_table_payload` when row anchors match |
| **Owned recovery / quirks apply tail** | Post-apply inference synthesizing ops not in the compiled ledger | Strict may block unless explicitly allowed; must emit `witness_rule_id`, finding, and regression | `fi.recovery.uncovered_body`; `fi.restructure.chapter_part_move_timeline`; `1988/1347` `section_snapshot_preserve_fold_for_descendant_scoped_source` |
| **Recovery guard** | Tightens an existing recovery rule's preconditions; does not add new mutation authority | Non-blocking observation; strict should prefer skip over false-positive mutation | `fi.restructure.chapter_part_move_timeline.label_reuse_guard` (`1929/234`) |
| **Comparison / adjudication** | Replay is source-faithful; gap is oracle/editorial/liite projection | N/A — not replay | `1982/91`, `1978/693`, `2021/617`, pass-3 structural-1.0 band |
| **Manual frontier / defer** | Source does not deterministically own oracle rows/text | Strict must block synthesis | `2001/823` sparse whole-section table rows omitted from amendment XML; `1991/1208` attachment/PDF lane |

**Rule of thumb:** if the fix **infers** state not named in a source instruction, it
belongs in the **recovery/quirks** lane with a stable `fi.*` rule id in
`spec_ledger.py`, a finding or pathology record, and strict-mode behavior spelled
out. If the fix **enforces** what the typed op already claimed, it is universal.

**Open family placements:**

- `2001/1234` → **fixed (universal)** snapshot/materialization (enforce `item:h` scope).
- `2001/823` → **quirks elaboration policy** (whether sparse table bodies may
  carry forward unchanged live rows — needs spec before apply synthesis).
- `1991/1208` partial province merge → **universal elaboration**
  (`fi.elaboration.named_row_province_table_merge`); residual lev → **manual
  frontier** (attachment-PDF table payloads `2001/995`+).
- `1929/234` part-move fix → **recovery guard** on existing
  `fi.restructure.chapter_part_move_timeline`, not a new universal move rule.
- `1929/234` part-insert subtree → **owned recovery** via
  `fi.recovery.uncovered_body.part_insert_subtree_johto_bypass` (johto + past-repeal
  bypass for `2001/1226` V osa payload).

## Aggregate

- Rows: 3545
- OK numeric rows: 3543
- Non-perfect Levenshtein rows: 1803
- Non-perfect Levenshtein with structural similarity 1.0: 876

Dominant overlapping diagnostics among non-perfect Levenshtein rows:

- `source_adjudication:oracle_suspect`: 1590
- `ELAB.SOURCE_PATHOLOGY`: 1538
- `ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE`: 1404
- `APPLY.UNCOVERED_BODY...`: 1360
- `structural:wording_text_changed`: 1063
- `ELAB.UNASSIGNED_SPARSE_SLOTS`: 1023

## Active Actionable Family: Sparse Parent/Descendant Materialization

### `1988/1347` — Ammattitautiasetus

- Run row: `amend=2`, structural similarity `1.000000`, Levenshtein `0.162257`.
- Relevant amendment: `2003/252`, `REPLACE section:3`.
- Source witness: `uv run lawvm source-dump 2003/252 --address 'section:3'`
  shows a sparse replacement section: section intro, omission markers, and
  explicit changed paragraphs/subparagraphs.
- Original failure witness:
  - `uv run lawvm snapshot-debug 1988/1347 --source 2003/252 --target section:3`
    emits a replacement payload whose text is only the section intro.
  - `uv run lawvm product-debug 1988/1347 --source 2003/252 --target section:3`
    shows child paragraph timelines still active at cutoff, but the selected
    parent section version materializes as only the intro.
- Self-consistency witness: `replay_fold_state` already had the full merged
  `3 §`, while `materialized_state` rebuilt the PIT from the sparse source
  shell and dropped the folded descendants.
- Root cause: group snapshot export treated descendant-scoped source text
  (`3 §:n ... 22, 23, 28, 30 ja 33 kohta ...`) as an exact whole-section
  source payload. The apply fold was correct enough to preserve the dense
  section, but timeline materialization consumed the sparse snapshot instead.
- Fix landed: section snapshot export now suppresses exact source-shell
  snapshots when the formula explicitly names descendant scope after a section
  reference, preserving the folded section and emitting
  `DESTRUCTIVE_SHAPE_LOSS_RISK` with recovery kind
  `section_snapshot_preserve_fold_for_descendant_scoped_source`.
- Regression: `tests/test_fi_materialization_invariants.py::
  TestNoDuplicatesInPIT::test_1988_1347_sparse_descendant_scoped_section_snapshot_keeps_fold`.
- Result: `uv run lawvm diff 1988/1347 --text` improved from section `3 §`
  at `1.8%` / statute score `75.44%` to section `3 §` at `95.5%` /
  statute score `98.86%`.
- Residual: `2003/252` still has sparse item-slot wording/frontier gaps
  (notably biological items 1 and 3 and comparison topology around category
  headings). This is no longer a wholesale materialization-loss case.

### `1966/612` — Asetus valtion eläkelain voimaanpanolain täytäntöönpanosta ja soveltamisesta

- Run row: structural similarity `1.000000`, Levenshtein `0.589378`.
- Relevant amendments: `1978/132` (`REPLACE section:2/subsection:2`) and
  `1984/270` (`INSERT section:2/subsection:3`).
- Source witness: base `1966/612` §2 encodes the first moment's items `2)`,
  `3)`, `4)`, and `5)` as sibling `<subsection>` nodes. The real second moment
  is the final unnumbered/content subsection. Later amendments correctly target
  `2 §:n 2 momentti` and then insert a new `3 momentti`.
- Root cause: source-ontology normalization did not fold a multi-subsection
  item run into the preceding list-bearing moment. The later `2 momentti`
  replacement therefore hit list item `2)` instead of the true second moment,
  dropping items `2)` through `5)` from materialized §2 and retaining the stale
  `Valtiovarainministeri ...` moment.
- Fix: `BASE_SECTION_ITEM_SUBSECTION_FOLD` folds consecutive section-level item
  carriers into the preceding subsection when they form one paragraph sequence,
  then relabels following true subsections in document order. This is emitted
  as a source-normalization fact, not a silent XML cleanup.
- Result: `uv run lawvm diff 1966/612 --text` improved from section `2 §` at
  `47.9%` / statute score `82.63%` to section `2 §` at `100.0%` / statute
  score `99.99%`. Remaining tiny text delta is source/oracle spelling and dash
  normalization (`diplomineri` vs `diplomimeri`, `1-5` vs `1–5`).
- Regression: `tests/test_fi_source_normalize.py::
  TestTagReclassify::test_folds_multi_subsection_item_run_and_relabels_true_moment`
  and `tests/test_fi_materialization_invariants.py::
  test_1966_612_section_item_subsection_fold_preserves_first_moment_items`.

### `1868/31-000` — Konkurssisääntö

- Run row (full bench `run_20260617T1132`): `amend=44`, structural similarity
  `0.913793`, Levenshtein `0.692415` (was `0.606019` / `0.905172` before §83
  letter-list fix).
- Relevant amendment for fixed subcase: `1993/1027`, complete `REPLACE
  chapter:6/section:85`.
- Source witness: `uv run lawvm source-dump 1993/1027 --address section:85`
  shows a complete short replacement for `85 §`.
- Original failure witness:
  - `uv run lawvm snapshot-debug 1868/31-000 --source 1993/1027 --target
    section:85` emitted the correct complete §85 snapshot.
  - `uv run lawvm product-debug 1868/31-000 --source 1993/1027 --target
    chapter:6/section:85` selected the later §85 version, but materialized the
    stale long §85 text from an earlier selected chapter snapshot.
- Root cause: the selected ancestor `chapter:6` snapshot from `1990/820` had
  duplicate direct `section:85` children. Core `apply_overlays` treated any
  direct child override under duplicate selected children as ambiguous and
  preserved the stale duplicates, even when the direct child override was an
  exact complete snapshot owner.
- Fix landed: core PIT materialization now lets an exact complete direct child
  overlay replace duplicate carried selected children once, emitting timeline
  issue `duplicate_selected_child_replaced_by_exact_child_overlay`. Ambiguous
  direct overrides without complete snapshot ownership keep the old
  preserve-duplicates behavior.
- Regression:
  - `tests/test_timeline_properties.py::
    test_materialize_body_child_replacement_overrides_duplicate_carried_snapshot_children`
  - `tests/test_fi_materialization_invariants.py::
    TestNoDuplicatesInPIT::test_1868_31_section_85_complete_child_overlay_replaces_duplicate_snapshot`
- Result: `uv run lawvm diff 1868/31-000 --text` improved from statute score
  `98.52%` to `99.24%`; §85 dropped out of the worst text differences.
  `oracle-check` improved from `42` to `41` diverging sections.
- Residual: many unrelated historical topology/source issues remain
  (`SOURCE_INCOMPLETE`, temporary rebase, missing XML topology, and old
  editorial convention sections). Do not treat this as a whole-statute fix.
- Follow-up fix (`1993/1027` `83 §`): johd + item `c` replaces failed on a
  letter-labelled moment (`a`/`b` only, no intro slot). Apply now prepends
  intro to letter-list moments and appends missing letter items
  (`intro_prepend_letter_list_moment`, `letter_item_replace_as_insert`).
- Post-fix check (`run_20260617T1129`): `83 §` perfect; statute section score
  `99.59%` (was `99.24%`); targeted bench Levenshtein `69.24%` (was `~59%`).
- Regression: `tests/test_fi_session_regressions_2026_04.py::
  test_1868_31_000_1993_1027_section_83_gets_intro_and_item_c`.
- Residual triage (`oracle-check` after §83 fix): `EDITORIAL_CONVENTION=29`,
  `ORACLE_STALE=2`, `SOURCE_INCOMPLETE=8`, `REPLAY_MISSING=1`, `EXTRA=1`.
  Worst compared text gaps (`24 §` `77.3%`, `84 §` `87.2%`, `60 §` `91.8%`) are
  oracle/editorial: source `1993/1027` fully replaces the implicated subsections
  while Finlex oracle still appends pre-amendment paragraphs (e.g. old `24 §`
  kiinteistö/pantti tail, old `84 §` tyytymättömyys rule). Seven
  replay-missing sections (`9 §`, `26 §`, `71 §`, `91 §`, `92 §`, `105 §`,
  `106 §`) are cross-reference stubs or HTML-topology gaps, not one replay bug.
  Status: **no further universal replay fix queued** for this statute band.

## Active Actionable Family: Named Table Row Scope

### `1991/1208` — Metsäveroasetus

- Run row: amendments `14`, structural similarity `0.920000`, Levenshtein
  `0.598139`.
- Current check: `uv run lawvm diff 1991/1208 --text` still reports statute
  score `91.92%`; the largest text gaps are table sections `13 §` and `14 §`.
- Source witnesses:
  - `1992/1009` formula: `13 §:n Kymen, Mikkelin, Kuopion, Vaasan ja Oulun
    lääniä koskevat kohdat sekä 15 §`.
  - `1993/994` formula: row-scoped `13 §` and `14 §` changes naming
    Uudenmaan, Turun ja Porin, Hämeen, Kymen, Mikkelin, Kuopion,
    Pohjois-Karjalan, Vaasan, Keski-Suomen, and Oulun province rows.
  - `1997/989` formula: row-scoped `13 §` and `14 §` changes naming
    Länsi-Suomen, Itä-Suomen, Oulun, Lapin, and Ahvenanmaan rows.
- Root cause (revised): regional johtolause parsing **does** tag
  `named_row_targets` on frontend ops (`_parse_regional_named_target_list` keeps
  compounds like `Turun ja Porin`). The remaining gap for partial updates is
  payload elaboration: province tables stay as `TABLE`/`ROW`/`CELL` IR, while
  `_rewrite_named_row_table_replaces` only handles paragraph `row_anchor` tables
  (käräjäoikeus family). A sparse payload such as `1992/1009` therefore
  overwrote whole `13 §` with only the five claimed province blocks.
- Fix landed (universal elaboration): `fi.elaboration.named_row_province_table_merge`
  merges only the `named_row_targets` province blocks from the amendment table
  into the live province layout; emits `ELAB.NAMED_ROW_PROVINCE_TABLE_MERGE`.
- Regressions:
  - `tests/test_fi_payload_normalize.py::
    test_normalize_group_payload_merges_named_row_province_table_blocks`
  - `tests/test_fi_session_regressions_2026_04.py::
    test_1991_1208_1992_1009_partial_province_table_merge_keeps_unclaimed_provinces`
- Residual lev frontier: late annual amendments `2001/995`+ ship omission XML
  with table bodies only in attachment PDFs (no executable payload in farchive).
  Full-replay `13 §`/`14 §` gaps (~17% section similarity) remain until an
  owned attachment-PDF payload lane exists. Section-level score stays ~`91.92%`.

## Active Actionable Family: Large Recodification / Migration

### `2017/320` — Laki liikenteen palveluista

- Run row (`run_20260617T1132`): amendments `54`, structural similarity
  `0.771429`, Levenshtein `0.607213`.
- Post-fix bench (`run_20260617T1157`): structural **83.55%**, Levenshtein
  **58.62%**; diagnostics include `APPLY.RELABEL_MIGRATION_LEDGER_LOOKUP×10`.
- Current check: `uv run lawvm diff 2017/320 --text` → `105` perfect,
  `70` replay-missing, `296` replay-extra; section score **90.02%** (up from
  `88.07%` pre-fix).
- `oracle-check`: `456` diverging — `EXTRA=175`, `ORACLE_STALE=82`,
  `SOURCE_INCOMPLETE=80`, `REPLAY_MISSING=20`, `EDITORIAL_CONVENTION=71`.
  Pathology families: `RECODIFICATION_SOURCE_CHAIN_GAP`,
  `CONTAINER_MEMBERSHIP_MISMATCH`, `SECTION_REPLACE_BOOTSTRAP_PARENT_MISSING`.
- Main recodification waves: `2019/371` (596 ops), `2020/1256`.
- `2019/371` phase witness:
  `uv run lawvm diagnose-phase 2017/320 --source 2019/371` → first bad phase
  `direct_applied_state` with **25** `duplicate_label` violations (e.g.
  `part:2/chapter:1` duplicate `section:16`, `part:2/chapter:2` duplicate
  `section:22`). Same-wave renumber + relabel is leaving stale and new labels
  cohabiting before fold.
- Relabel skip families (already regression-owned in
  `tests/test_fi_restructure_plan.py`):
  - `part:2a` → `target_part_absent_in_pre_partification_frame` (Part IIa
    frame not present before partification relabel chain);
  - `part:6/chapter:2/section:7` → `target_leaf_absent_under_existing_parent`
    + `RECODIFICATION_SOURCE_CHAIN_GAP`;
  - `2020/1256` `part:3/chapter:1`/`chapter:2` → **fixed** via
    `fi.restructure.relabel_migration_ledger_lookup` (ledger `as_of_date`, not
    `not_before` — prior `not_before=effective_date` wrongly excluded
    `2019/371` part III→IV migrations).
  - `2019/371` `part:2a` → **fixed** via
    `fi.restructure.relabel_structural_label_alias_lookup` (live tree label
    `iia` vs amendment-frame `2a`; `_find_path_by_suffix` now matches through
    `_norm_num_token`).
- Missing oracle sections `209 §`–`215 §` witness (refined 2026-06-17):
  source `2019/371` carries them under amendment-body `4 luku`
  (`Julkisen hallintotehtävän antaminen muulle toimijalle`). `209`, `210`,
  `212`–`215` are omission-only shells; `211` and `212` carry partial
  subsection source. Oracle editorial consolidation has full text (manual
  frontier / `SOURCE_INCOMPLETE` — do not invent).
  Replay fold **does** carry `part:5/chapter:25/section:209`–`215` after the
  full chain (first appears before `2021/91`), but `lawvm diff` compares
  PIT-materialized `master.ir` (`build_full_products=True`) where those
  addresses have **zero** `lo_ops`/timeline versions → `materialize_pit`
  drops them → diff reports `MISSING`. `211 §` at 65.2% pairs oracle
  `part:5/chapter:25/section:211` with stale replay `part:2/chapter:4/section:211`
  when materialized ch25 is absent. Family: fold-only apply without timeline
  snapshot emission, not a single bad REPLACE.
- Existing regressions already pin subsets:
  `test_process_muutoslaki_2017_320_2019_371_recodification_regressions`,
  `test_2017_320_2019_371_*` relabel fixtures,
  `test_2017_320_2020_1256_vi_chapter_26_28_relabels_execute_in_part_vi`.
- Fixes landed (phase: apply/restructure):
  1. **Migration ledger lookup** — `fi.restructure.relabel_migration_ledger_lookup`
     / `APPLY.RELABEL_MIGRATION_LEDGER_LOOKUP`
  2. **Structural label alias** — `fi.restructure.relabel_structural_label_alias_lookup`
     / `APPLY.RELABEL_STRUCTURAL_LABEL_ALIAS_LOOKUP` (`part:2a` ↔ live `iia`)
  - Tests: synthetic + `test_2017_320_2019_371_part_iia_relabel_*`,
    `test_2017_320_2020_1256_chapter_15_16_*`, migration-ledger synthetics
  - Strict mode: proceeds with owned observation (not a fallback guess)
  - Post-fix diff: section score **90.56%** (was `88.07%` pre-fix band)
- Fix landed (phase: apply/process, family: post-apply label dedup):
  - Rule: `fi.process.post_apply_label_dedup`
  - Finding: `APPLY.GLOBAL_LABEL_DEDUP_APPLIED` with
    `phase=process_muutoslaki.post_apply`
  - `diagnose-phase 2019/371 --detector duplicate_label` now **all phases clean**
    (was 25–49 transient violations at `direct_applied_state`)
- Root-cause class (remaining):
  1. omission-only / partial source payloads (`209§`–`215§`) →
     `SOURCE_INCOMPLETE` / manual frontier;
  2. fold-vs-materialized gap: section nodes in `replay_fold_state` without
     matching timeline `lo_ops` at `part:5/chapter:25` (PIT drops them);
  3. `RECODIFICATION_SOURCE_CHAIN_GAP` skip for `part:6/chapter:2/section:7`.
- 2026-06-19 refresh (`run_20260619T0310` plus current-tree targeted diff):
  section score is now about `90.91%`; the `209§`–`215§` fold/materialization
  gap is no longer the visible top failure in the dirty current tree. The
  remaining lowest-similarity rows are mostly text-present-under-stale-container
  cases (e.g. oracle `part:2/chapter:2/section:8` text exists in replay at
  `part:2/chapter:4/section:8`), i.e. container-membership/recodification
  topology rather than missing text.
- Bounded relabel-miss audit for the three visible `2019/371` warnings:
  - `part:5/chapter:4/section:2 -> section:234`: source explicitly says
    `4 luvun ... 2 §:n numero 234:ksi` and the body carries full `234 §`
    text. Pre-wave `part:5/chapter:4` has no section `2`; the plausible
    old source under the part-migration frame was already consumed by the
    separate `-> section:210` relabel. Reusing it would violate no double
    consumption / no target hijacking.
  - `part:5/chapter:5/section:2 -> section:237`: source explicitly says
    `5 luvun 1 ja 2 §:n numero 236 ja 237:ksi`, but body `237 §` is
    omission-only. Keep `RECODIFICATION_SOURCE_CHAIN_GAP`.
  - `part:6/chapter:2/section:7 -> section:268`: source explicitly says
    `2 luvun ... 7 §:n numero 268:ksi`; body `268 §` has only partial
    subsection payload plus special commencement. Existing pending-lineage
    handling is the right current behavior; do not broaden lookup.
- Possible future fix: a named source-chain payload-bootstrap family for
  failed relabel + same-source full destination body payload. It may apply to
  `234` only, with strict-mode barrier, explicit finding/rule ID, no
  double-consumption, negative tests for omission-only `237`, and corpus tests
  preserving `268` as deferred lineage/source-chain gap.
- Status: **partial fix landed** (relabel ledger, `part:2a` alias, post-apply
  dedup); next family fix is owned timeline snapshot emission for recovered /
  late-chain inserts or a carefully owned source-chain payload bootstrap — not
  oracle text injection or target lookup broadening.

## Fixed Actionable Family: Cross-Act Repeal Routing

### `1978/611` — Veronkantolaki

- Run row: amendments `33`, structural similarity `0.958333`, Levenshtein
  `0.622832`.
- Pre-fix check: `uv run lawvm diff 1978/611 --text` reported statute score
  `98.92%`, but `22` replay-extra sections. `oracle-check` classified the run
  as `EXTRA=10`, `ORACLE_STALE=11`, `REPLAY_EXTRA=1`, and
  `SOURCE_PATHOLOGY=1`.
- Concrete source witness: `1998/532` (`Verontilityslaki`) §25(2)(1) says
  `Tällä lailla kumotaan ... veronkantolain (611/1978) 2 a ja 3 luku ja 30 §:n
  2 ja 3 momentti ...`.
- Root cause: the VTS extractor recognized paragraphized repeal-like source but
  required the repeal verb to appear inside the same numbered paragraph as the
  parent citation. In this source, the repeal authority is the governing
  subsection intro `Tällä lailla kumotaan:`, while the parent citation and
  targets are in paragraph `1)`.
- Fix: governed paragraphs under a `kumotaan` intro now inherit that repeal
  authority for VTS fragment extraction. The compiled ledger emits explicit
  `fi.repeal_vts_voimaantulo` operations from `1998/532`: `REPEAL chapter:2a`,
  `REPEAL chapter:3`, `REPEAL section:30 / subsection:2`, and
  `REPEAL section:30 / subsection:3`.
- Post-fix check: `uv run lawvm ops 1978/611 --source 1998/532` shows those
  four operations; `uv run lawvm diff 1978/611 --text` now reports `27`
  perfect sections out of `28`, with one replay-extra section; `oracle-check`
  classifies the remaining four divergences as `EDITORIAL_CONVENTION=3` and
  `ORACLE_STALE=1`.
- Tests: `tests/test_fi_vts.py` pins the governed-list extractor shape and
  real `1998/532`; `tests/test_fi_session_regressions_2026_04.py` pins full
  replay routing through the compiled operation ledger.

## Fixed Actionable Family: Flattened List Tail Preservation

### `2017/93`

- Run row: structural similarity `0.966667`, Levenshtein `0.761588`.
- Relevant amendment: `2018/1243`, replacement of the comparable first
  subsection/list payload.
- Root cause: `ELAB.COLLAPSE_FLATTENED_FIRST_SUBSECTION_LIST` ran even when
  the payload already had a structured first subsection and no flattened sibling
  rows. The normalizer moved a trailing omission marker inside subsection `1`,
  which made sparse-tail alignment preserve stale live item `10`.
- Fix landed: flattened-list collapse now requires actual flattened sibling
  rows before moving trailing omission markers into the first subsection. An
  already-structured first subsection with a trailing omission remains a sparse
  payload, so the replacement drops the stale tail item instead of re-splicing
  it.
- Regression: `tests/test_fi_payload_normalize.py::
  test_flattened_first_moment_collapse_ignores_already_structured_trailing_omission`
  and `tests/test_fi_session_regressions_2026_04.py::
  test_2017_93_bench_comparable_first_subsection_replace_drops_stale_flattened_tail`.
- Result: direct structural comparison for the bench-comparable section is now
  exact.

## Deferred / Not Immediate Replay Fix

### `1979/925` — Sähköasetus

- Run row: structural similarity `0.973684`, Levenshtein `0.415520`.
- `oracle-check`: `EDITORIAL_CONVENTION=5`, `EXTRA=15`, `ORACLE_STALE=9`;
  source-pathology includes `DESTRUCTIVE_SHAPE_LOSS_RISK`.
- Status: not a single replay fix. Revisit after sparse materialization and
  source/oracle adjudication improvements.

### `2001/1047` — Arpajaislaki

- Run row (`run_20260618T1324`): amendments `21`, structural similarity
  `0.877551`, Levenshtein `0.703841`.
- Fixed bounded subcase: `2021/1284` `14 b §` has a numbered-item list followed
  by an unnumbered trailing content paragraph. Semantic projection already
  classifies that trailing content as `wrapUp`, but also serialized it as a
  `wording` facet, producing a false `wording_text_changed` event in structural
  review. Projection now excludes trailing wrap-up candidates from wording
  serialization; `structural-review 2001/1047 --dump` no longer shows the
  `14 b §` duplicate-tail event.
- Regression: `tests/test_semantic_structure.py::
  test_ir_projection_structured_wrapup_does_not_duplicate_aggregate_text_as_wording`.
- Additional bounded fix landed: foreign-scoped whole-section replacements now
  own carried non-member sections inside broader chapter payloads. This prunes
  stale old rahapeli sections carried through chapter payloads while preserving
  new unowned container members and descendant-only replacements. After
  `run_20260618T1405`, row improved to structural `0.975904`, Levenshtein
  `0.929047`.
- Residual: `structural-review 2001/1047 --dump` now shows only the
  `24 §` heading hyphenation and `62 h §` future repeal/editorial-display
  mismatch around the 2025/479/2026 in-force surface.
- 2026-06-19 refresh (`run_20260619T0310`): row is still high in the
  full-text Levenshtein list (`0.825075`), but section diff is `97.99%`.
  The visible residuals are dominated by `2026/11` future-effective editorial
  stubs and future payload shown in the official-consolidation oracle
  (`voimaan 1.7.2027`) at a 2026 surface. Do not pull those into legal-PIT
  replay without a broader oracle/editorial-future display rule.
- Status: bounded replay/payload ownership issue fixed; residual remains
  comparison/future-repeal/editorial display topology.

### `2021/1289`, `2003/1129`, `2004/1287` — metric-surface rows

- 2026-06-19 refresh: these statutes appear high in the full-text Levenshtein
  list (`2021/1289` `0.794635`, `2003/1129` `0.815668`, `2004/1287`
  `0.816122`), but targeted section diffs report every compared section
  perfect:
  - `lawvm diff 2021/1289 --text --compile-summary` → `23 compared`,
    `23 perfect`, score `100.00%`.
  - `lawvm diff 2003/1129 --text --compile-summary` → `30 compared`,
    `30 perfect`, score `100.00%`.
  - `lawvm diff 2004/1287 --text --compile-summary` → `40 compared`,
    `40 perfect`, score `100.00%`.
- Disposition: skip for replay. These are metric/comparison-surface artifacts,
  not source-owned legal-state repairs.

### `1990/1271` — Osuuspankkilaki

- Run row (`run_20260618T1341`): amendments `9`, structural similarity
  `0.970588`, Levenshtein `0.790351`.
- Root cause: `1996/575` replaces chapter 7 but the body carries sections
  `51 §` and `61 §` whose explicit ops are scoped to chapter 8. The broad
  chapter payload preserved those foreign-scoped carried sections as
  `chapter:7/section:51` and `chapter:7/section:61`; later unscoped/standalone
  changes then attached to the stale chapter-7 copies rather than the intended
  chapter-8 sections.
- Fix landed: the same foreign-scoped whole-section replacement ownership rule
  prunes `51`/`61` from the chapter-7 payload. `structural-review 1990/1271
  --dump` no longer reports `chapter:7/section:51` or `chapter:7/section:61`
  as extra sections; focused bench is about structural `0.98`, Levenshtein
  `0.795-0.797`.
- Residual: section `93 §` retains an editorial topology mismatch around
  repealed items `2`/`5` and a trailing wrap-up rendered as `2 mom.` vs
  Finlex `loppukappale`; additional extras under `6a/8a` are a separate
  chapter-placement/display family.
- Status: bounded carried-section ownership issue fixed; residual deferred.

### `2003/167`

- Run row: structural similarity `1.000000`, Levenshtein `0.676953`.
- Original old-run check: one replay-extra section `2 §`, classified as
  `ORACLE_STALE=1` / HTML-topology `missing_from_xml=2 §`.
- Current check: `uv run lawvm bench --statute 2003/167 --no-save --top 5`
  and `uv run lawvm diff 2003/167 --text --threshold 0.999` are perfect on
  current code.
- Status: stale old-run row; no current replay fix.

### `2003/513`

- Run row: structural similarity `1.000000`, Levenshtein `0.732184`.
- Current check: `uv run lawvm bench --statute 2003/513 --no-save --top 5`
  reports perfect structural and Levenshtein agreement; `uv run lawvm diff
  2003/513 --text --threshold 0.999` compares sections `1` and `3` exactly.
- Source witness: `finlex://sd/2006/1114/fin/main.xml`, section `26`,
  paragraph/list item `5`, repeals `(513/2003) 2 §`; `uv run lawvm ops
  2003/513 --source 2006/1114` emits `REPEAL section:2`.
- Oracle witness: `finlex://sd-cons/2003/513/fin@20061114/main.xml` carries
  `section eId="sec_2v20061114"` with the editorial repeal note for `2 §`.
- Residual: `oracle-check` still classifies one editorial convention /
  HTML-topology gap because the cached HTML exposes the repealed-section note
  while the comparable product suppresses tombstones.
- Status: stale old-run row; keep as an evidence fixture for repeal-in-
  commencement-body lists and editorial PIT timing, not a replay fix.

### `2011/1546`

- Run row: structural similarity `1.000000`, Levenshtein `0.726244`.
- Current check: `uv run lawvm diff 2011/1546 --text --threshold 0.999`
  reports five compared sections and five perfect sections.
- Status: stale old-run row; no current replay fix.

### `2017/277`

- Run row: structural similarity `1.000000`, Levenshtein `0.733754`.
- Current check: `uv run lawvm bench --statute 2017/277 --no-save --top 5`
  reports perfect primary structural similarity and Levenshtein `97.92%`.
- Residual witness: structural review shows `4 §` missing Finlex subsection `2`
  text, `Todennäköisesti merkittävien ...`.
- Source witness: `finlex://sd/2021/1163/fin/main.xml` says only
  `muutetaan ... 4 §:n 1 momentti seuraavasti:` and contains no subsection `2`
  body. The consolidated oracle
  `finlex://sd-cons/2017/277/fin@20211163/main.xml` carries unversioned
  `section eId="sec_4" / subsection eId="sec_4__subsec_2"` text.
- Status: not a source-owned amendment payload fix. This is carried
  consolidated baseline/current material that should be handled by source/oracle
  adjudication or baseline import policy, not by synthesizing extra payload from
  `2021/1163`.

### `1992/1535` — Tuloverolaki

- Run row (`run_20260617T1132`): structural similarity `0.908213`, Levenshtein
  `0.727505`.
- Current checks are split: `uv run lawvm diff 1992/1535 --text --threshold
  0.999` reports statute score `99.04%`, while bench full-text Levenshtein is
  still `72.79%` because the official consolidation surface includes many
  editorial repeal notices, future-effective stubs, and previous-wording
  projections.
- Source witness: `finlex://sd/2025/377/fin/main.xml` explicitly takes effect
  on `1.1.2027` and repeals/replaces pension and deduction provisions, including
  `34 b §:n 2 momentti`, `54 d §`, `96 a §`, `131 a §`, `132 §:n 2 momentti`,
  `133 §:n 2 momentti`, and `134 §:n 2 momentti`.
- Replay witness: `uv run lawvm product-debug 1992/1535 --source 2025/377
  --target section:34a` and the analogous checks for `34b` and `131a` select
  the pre-2027 legal PIT text at the 2026 cutoff while recording the 2027
  future versions/tombstones.
- Status: mostly comparison-surface/adjudication work, not a replay mutation
  fix. Do not force legal-PIT replay to match future/editorial Finlex notices.

### `1929/234` — Avioliittolaki

- Run row before fix: structural similarity `0.960000`, Levenshtein
  `0.667098`.
- Main failing source wave: `2001/1226`.
- Source witness: preamble `lisätään lakiin siitä mainitulla lailla 411/1987
  kumotun V osan tilalle uusi V osa seuraavasti:` compiles `INSERT 5 osa`
  plus unrelated section/subsection replacements.
- Root cause: post-apply supplemental recovery treated same-numbered chapter
  labels in a genuinely new part as cross-part moves, emitting
  `REPEAL part:2/chapter:4`, `REPEAL part:4/chapter:1`, etc., even though
  those chapters still lived under their original parts. That silently deleted
  unrelated legal state.
- Fix landed: guard on recovery rule
  `fi.restructure.chapter_part_move_timeline.label_reuse_guard` — inferred
  part-move timeline LOs now require the pre-amendment chapter to be absent
  from its old part. Label reuse across parts during part INSERT no longer
  triggers global reconciliation. Suppressions emit `APPLY.MOVE_SKIP` with
  `reason_code=chapter_label_reuse_old_part_still_hosts_chapter`. This is
  **quirks-mode recovery guard**, not universal move semantics.
- Regression:
  `tests/test_fi_replay_products.py::
  test_replay_xml_1929_234_part_v_rebirth_does_not_repeal_unrelated_part_chapters`.
- Result: `uv run lawvm bench --statute 1929/234 --no-save` improved to
  structural `89.47%`, Levenshtein `79.52%`; `uv run lawvm diff 1929/234
  --text --threshold 0.999` reports `97.99%` with `4` replay-missing and `17`
  replay-extra (was `32` missing / `14` extra).
- Part-insert subtree fix (`2001/1226`): uncovered-body recovery skipped part V
  sections because johtolause only named `6 §`, `12 §`, `27 §` while the payload
  used a `crossHeading` part marker (`V OSA`) rather than `<part>`. Fix:
  `part_insert_labels` johto bypass, `part_insert_subtree` past-repeal bypass,
  and `xml_part_label` cross-heading context. Witness sections `110`–`113 §`
  now materialize.
- Regressions:
  `tests/test_fi_replay_products.py::
  test_replay_xml_1929_234_materializes_part_v_after_2001_1226` (extended to
  `110`–`113 §`),
  `tests/test_fi_uncovered_recovery_helpers.py` (part-insert label gate +
  cross-heading part label).
- Result: `uv run lawvm diff 1929/234 --text --threshold 0.999` → **98.82%**
  (0 replay-missing vs 4 before this pass); bench Levenshtein `0.821092` (was
  `0.801350` in `T1058`).
- Residual: editorial wording deltas, replay-extra topology under other parts,
  and sparse johto-only gaps outside part-insert payloads.

### `1987/322`

- Run row: structural similarity `1.000000`, Levenshtein `0.682632`.
- Source witness: `2023/741` repeals `10 a-10 f §`; the selected section
  timelines had explicit `lawvm_repeal_placeholder=1` content effective
  `2024-01-01`.
- Root cause: PIT overlay treated independently active descendant timelines as
  eligible children even when the selected parent content was a repeal
  placeholder. That rehydrated old subsection text under the tombstoned
  sections, leaving section-key structure perfect but full-text Levenshtein
  poor.
- Fix family: core timeline materialization now treats a selected repeal
  placeholder as a descendant-overlay barrier. Regression tests cover both a
  synthetic parent tombstone with stale child timeline and corpus statute
  `1987/322`.
- Targeted verification: `uv run lawvm bench --statute 1987/322 --no-save
  --top 5` now reports structural `100.00%` and Levenshtein `100.00%`.

### `2015/234`

- Run row: structural similarity `1.000000`, Levenshtein `0.799378`.
- `diff --text`: oracle has `13 §` and `15 §` missing from replay.
- `oracle-check`: `ORACLE_STALE=2`, `EDITORIAL_CONVENTION=1`;
  source-pathology includes `DESTRUCTIVE_SHAPE_LOSS_RISK` and
  `TEMPORARY_SECTION_REBASE`.
- Status: temporal/rebase proof required; not a text patch.

## Fixed Actionable Family: Permanent PIT Restoration After Temporary Overlays

### `1998/805`

- Run row: structural similarity `1.000000`, Levenshtein `0.803614`.
- Current failing witness before fix: `uv run lawvm diff 1998/805 --text
  --threshold 0.999` missed `1 §` and `2 §` after expired temporary chains.
- Source witness: `2015/611` permanently replaces `1-3, 3 a, 4 §` and inserts
  `1 a` and `6 a`; later `2020/200`, `2020/364`, `2020/614`, and `2021/508`
  apply temporary overlays that expire before the 2026 bench cutoff.
- Root cause: `materialize_pit_ex` contained a broad inferred suppression rule
  for "permanently-introduced-as-temporary" versions. It marked a selected
  permanent version inactive whenever all later versions were expired temporary
  overlays and no later permanent version existed. That inference erased
  source-owned permanent text without an explicit absence projection.
- Fix landed: removed the broad inferred suppression. Explicit absence
  projection remains owned by the
  `lawvm_materialize_as_absent_under_detached_horizon` attr used by zero-day
  repeal placeholders.
- Regression: `tests/test_timeline_properties.py::
  test_materialize_pit_restores_base_owned_permanent_amendment_after_temporary_chain`,
  `tests/test_timeline_properties.py::
  test_materialize_pit_restores_permanent_inserted_descendant_after_temporary_chain`,
  and `tests/test_fi_replay_products.py::
  test_1998_805_materialized_state_restores_sections_after_expired_temporary_chain`.
- Result: `uv run lawvm bench --statute 1998/805 --no-save --top 5` and
  `uv run lawvm diff 1998/805 --text --threshold 0.999` are both perfect.

## Deferred / Not Immediate Replay Fix

### `2015/1635`

- Run row: structural similarity `1.000000`, Levenshtein `0.758816`.
- Current replay is source-faithful for the chapter 3 repeal. The old low row
  came from a stale comparable oracle lane: bench-comparable selected
  `fin@20221289`, while the later consolidated surface `fin@20230741` reflects
  `2023/741` and matches the replayed chapter 3 repeal.
- Status: comparison-lane freshness/adjudication issue, not a replay mutation
  fix.

### `1992/1702`

- Run row: structural similarity `0.770492`, Levenshtein `0.759513`.
- Current check: `uv run lawvm bench --statute 1992/1702 --no-save --top 5`
  remains low; `uv run lawvm diff 1992/1702 --text --threshold 0.999` reports
  many stale replay extras and mismatches.
- Root-cause direction: large recodification/rechaptering around `1995/1599`,
  including a broad `REPLACE chapter:3`, later chapter-scoped replacements, and
  replay extras under old `chapter:3` that appear to correspond to later
  chapter 8 material.
- Diagnostics: `oracle-check` classifies a mix of `EDITORIAL_CONVENTION`,
  `EXTRA`, `ORACLE_STALE`, `REPLAY_MISSING`, and `SOURCE_INCOMPLETE`, with
  source pathologies including `DESTRUCTIVE_SHAPE_LOSS_RISK`,
  `EMPTY_OPERATIVE_BODY`, `SUBSECTION_TARGET_ABSENT`, and
  `SUBSECTION_TARGET_REBOUND`.
- Status: defer as a dedicated recodification/chapter-replacement and migration
  family. This should not be solved by local text or target guessing.

## Fixed Actionable Family: Exact Temporary Provision Expiry

### `2017/236`

- Run row: structural similarity `1.000000`, Levenshtein `0.806595`.
- Current failing witness before fix: `4-7 §` replay carried 2025 temporary
  fishing-rule moments into the `fin@20251135` comparison state; `section:7`
  also re-emitted expired `4 mom.` as a fresh 2025/1135 permanent child
  snapshot.
- Source witness: `finlex://sd/2025/236/fin/main.xml` states that `3 §:n
  otsikko`, `3 ja 4 momentti`, `4 §:n 3 ja 4 momentti`, `5 §:n 4-6 momentti`,
  `6 §:n 4-6 momentti`, `7 §:n 4 momentti`, and `8 §:n 3 momentti` are in
  force through `31.12.2025`.
- Root cause: Finland parsed the temporary johtolause and emitted temporal
  expire events, but exact subsection/facet expiry was not carried into child
  snapshot sources. A later section snapshot from `2025/1135` then minted a new
  permanent `section:7/subsection:4` version from carried expired text.
- Fix landed: added exact temporary provision expiry metadata for mixed
  heading/moment sunset clauses; section child snapshots now use a matching
  subsection op's source when it carries narrower expiry authority, and carried
  section snapshots drop expired temporary subsection children with a typed
  source-pathology record.
- Regression: `tests/test_fi_metadata_temporal.py::
  test_temporary_provision_expiry_overrides_parse_mixed_heading_and_moment_scope`
  and `tests/test_fi_replay_products.py::
  test_2017_236_materialized_state_drops_expired_exact_temporary_moments`.
- Result: `uv run lawvm bench --statute 2017/236 --no-save --top 5` and
  `uv run lawvm diff 2017/236 --text --threshold 0.999` are both perfect.

## Deferred / Not Immediate Replay Fix

### `1991/1208`

- Run row: structural similarity `0.920000`, Levenshtein `0.598139`.
- Post partial-province-merge check (`run_20260617T1119`): full-text lev
  `59.81%` unchanged; section-level `91.92%`; `ELAB.NAMED_ROW_PROVINCE_TABLE_MERGE×2`
  on `1992/1009` path.
- Partial-update subcase **fixed**: `1992/1009` no longer truncates `13 §` to
  five province blocks (regression pins all eleven live provinces retained).
- Residual lev frontier: acquisition/payload extraction. Late annual table
  amendments such as `2001/995`, `2003/917`, `2004/958`, and `2005/873` expose
  only omission XML plus attachment PDFs in farchive, so LawVM compiles no
  executable table payload for the source-owned updates.
- Status: defer remaining lev gap to an owned attachment-PDF payload/source-
  pathology lane. Do not narrow/delete replayed table text to match an XML
  oracle that lacks the attached payload.

### `1982/182`

- Run row: structural similarity `0.548387`, Levenshtein `0.775625`.
- Current single-statute bench: full-text about `77.59%`, section-level about
  `86.14%`; `oracle-check` reports a mix of editorial convention, missing,
  stale, replay-extra, replay-missing, and source-incomplete cases.
- Root-cause direction: traffic-sign catalog topology and image-backed payload
  extraction. Source acts serialize sign catalogs as sparse subsection/image
  sequences while the consolidated oracle normalizes them into nested catalog
  paragraphs/subparagraphs with editorial repeal notices.
- Status: defer to a named catalog-payload elaboration family with strict-mode
  barriers for image-only witness gaps. The bounded `51 §:n 1 momentti` target
  subcase can be revisited separately, but it is not the dominant score driver.
- 2026-06-19 refresh (`run_20260619T0310`): this became the current worst row
  after unrelated source-faithful topology changes: structural `0.548387`,
  Levenshtein `0.542737`; `oracle-check` reports `54.2%` over `35` diverging
  sections (`EDITORIAL_CONVENTION=14`, `SOURCE_INCOMPLETE=9`,
  `REPLAY_MISSING=5`, `MISSING=4`, `ORACLE_STALE=2`, `REPLAY_EXTRA=1`) with
  `DESTRUCTIVE_SHAPE_LOSS_RISK` and `OMISSION_SURVIVES_MERGE`.
- Bounded witness check:
  - `1990/934` does compile a source-owned `REPLACE section:27`, but the source
    XML contains only the `27 §` intro plus an image block
    (`media/0263.gif`). The oracle text expands that image into three numbered
    traffic-light meanings. Reconstructing those items would require an owned
    image/OCR payload lane; replay must not inject oracle-only text.
  - Missing `4 luku / 24 §`, `25 §`, `29 §`, and `30 §` have no compiled
    amendment operations. The base `1982/182` XML itself jumps from chapter
    3 to chapter 8; chapter 4 is absent from the machine source. These are base
    acquisition/source-completeness gaps, not resolver misses.
- Disposition remains **defer, do not patch replay text**. Any future work
  should be an explicit image-backed/source-completeness acquisition lane with
  named source-pathology evidence and strict-mode behavior.

### `1953/317` — Laki vaarallisten rikoksenuusijain eristämisestä

- Run row before fix (`run_20260618T1451`): structural similarity `0.857143`,
  Levenshtein `0.787909`; post-fix single-statute row (`run_20260618T1459`):
  structural `0.904762`, Levenshtein `0.788103`.
- Fixed subcase: `2 luku / 7 §` compared stale replay wording
  (`rikoslain 3 luvun 11 §`) against oracle wording from `2003/537`
  (`rikoslain 6 luvun 13 §`).
- Source/oracle witness: selected consolidated artifact
  `finlex://sd-cons/1953/317/fin@20050786/main.xml` has
  `dateConsolidated=2003-06-13`, but the current oracle `7 §` section carries
  `finlex:originalVersion="@20030537"` and the exact `2003/537` text.
- Root cause: official-consolidation horizon selection suppressed same-day
  future-effective `2003/537` because its effective date is `2004-01-01` and
  the artifact cutoff says `2003-06-13`, even though the provision body itself
  is explicitly materialized from `2003/537`.
- Fix landed: horizon selection now accepts a narrow oracle-body witness set
  from section-level `finlex:originalVersion` attributes and uses it only to
  bypass the existing future-effective suppression for those exact source acts.
  Future-repeal overlay sections containing `tulee voimaan` are excluded so
  the `1992/785`/`2000/812` future-repeal guard remains intact.
- Regressions:
  - `tests/test_fi_corpus.py::
    test_oracle_reflected_section_original_versions_excludes_future_repeal_overlay`
  - `tests/test_fi_materialization_invariants.py::
    TestNoDuplicatesInPIT::test_1953_317_reflected_section_original_version_extends_oracle_horizon`
  - existing guard tests for `1992/785` and `2000/812`.
- Residuals: `1 §` is an item-scoped sparse replacement / section-snapshot
  family (`1995/591` only claims `section:1/subsection:1/item:1` but snapshots
  a reconstructed whole section); `5 §` is tied to old wording / subsection
  repeal (`1976/466`); extra `20 §` is `html-topology: missing_from_xml`.
  Do not patch these by text injection or broad horizon changes.

## Fixed Actionable Family: Flat Section Replace Scope From Live Chapter Gaps

### `1919/1-001`

- Run row before fix: structural similarity `0.745455`, Levenshtein
  `0.804170` in `data/bench_runs/20260617T0504_run_20260617T0504.csv`.
- Symptom: `lawvm diff 1919/1-001 --text --threshold 0.999` was almost
  perfect at section text level, but full-text Levenshtein was low. The
  physical replay tree had later replacement sections (`1`, `11`, `21`, `37`,
  `38`, `41`, `42`, `44`, `49`) appended as root-level sections after chapter
  9, so whole-statute serialization was out of order.
- Source witness: source acts such as `1930/272`, `1966/175`, `1980/421`,
  `1989/1333`, and `1993/308` say only `N §` and carry flat whole-section
  payloads. The base XML omits some original sections, but surrounding live
  chapter sequences prove internal gaps or a first-section head gap.
- Fix landed: compile-time rule
  `fi_flat_body_replace_scope_from_live_section_gap` infers chapter scope only
  for flat whole-section `REPLACE` payloads where the live chapter sequence
  identifies a unique safe gap. Singleton boundary gaps, such as `49` between
  sections `48` and `50`, stay unresolved rather than guessing.
- Regression: `tests/test_fi_compile.py::
  test_replay_xml_1919_1_scopes_flat_replaces_into_live_chapter_gaps`.
- Result: targeted bench Levenshtein improved from `80.42%` to `96.54%`;
  structural similarity remained `74.55%`, consistent with residual wording /
  editorial differences rather than full-text order drift.

## Fixed Actionable Family: VTS Mixed Genitive Tail Address Parsing

### `1979/319`

- Run row before fix: structural similarity `0.985507`, Levenshtein
  `0.818584` in `data/bench_runs/20260617T0535_run_20260617T0535.csv`.
- Symptom: section-level diff had no replay-missing sections but 21 replay
  extras; full-text Levenshtein was low because large repealed portions of the
  old electricity act still serialized.
- Source witness: `1995/386` section 55 says `Tällä lailla kumotaan ...`
  `1979/319` `1 §:n 2 momentti`, `2 §:n 1 momentin 5 kohta`, `3 §`,
  `2-5 luku`, `30 §:n 2 momentti`, `60 §:n 3 momentti` and `64, 66, 68 ja
  69 §`.
- Root cause: `address_parse.parse_legal_addresses` parsed the first genitive
  target in a mixed VTS fragment and consumed the rest of the fragment, so
  later section/chapter targets were invisible to VTS lowering.
- Fix landed: genitive-tail parsing now hands unconsumed trailing text back to
  the structured address parser. This preserves the existing `2023/741`
  trailing-section-range behavior and recovers the later targets in `1995/386`
  without an apply fallback.
- Regression: `tests/test_fi_vts.py::
  test_extract_voimaantulo_repeals_keeps_mixed_later_targets_after_genitive_refs_real_corpus`.
- Result: targeted bench improved from structural `98.55%`, Levenshtein
  `81.86%` to structural `100.00%`, Levenshtein `99.92%`.

## Deferred / Granularity Frontier

### `2000/1106`

- Run row: structural similarity `0.750000`, Levenshtein `0.806572`.
- Root-cause direction: Finnish `alakohta` targets are lost before replay.
  Source `2002/276` and `2004/1265` name subitems such as `1 §:n 2 kohdan h
  alakohta` and `3 §:n ... 2 kohdan a ja d alakohta`, but extraction/lowering
  emits parent item operations (`section:1/subsection:1/item:2`,
  `section:3/subsection:1/item:1`, etc.).
- Diagnostics: targeted bench reproduces the row with `unit_missing_left`,
  `ELAB.SOURCE_PATHOLOGY`, and `text_duplication_warning`; strict report
  blocks the recoveries with source-pathology / strict-rejected / failed-op
  reasons.
- Status: defer to first-class Finnish `alakohta` target/payload elaboration
  through ClauseAST/PayloadIR/lowering or an explicit unsupported-subitem
  barrier. Do not add an apply fallback that mutates parent `kohta` state from
  child `alakohta` source authority.

### `1999/329`

- Run row: structural similarity `0.960000`, Levenshtein `0.829504`.
- Root-cause direction: same-act chapter repeal/restructure and missing
  lineage. `2007/1479` repeals `3 ja 4 luku`, changes `2 luvun otsikko, 10,
  11 ja 15 §`, and later surviving chapters are renumbered in the Finlex
  consolidated surface.
- Evidence: replay emits `REPEAL chapter:3`, `REPEAL chapter:4`, but also
  `REPLACE chapter:3/section:15` and keeps `section:65` under `chapter:7`
  where Finlex has it under `chapter:6`; `phase-witness` reports no migration
  events.
- Status: defer to a chapter-restructure/lineage family with explicit
  migration events and strict-mode behavior. A one-off section retarget would
  be target hijacking and would not account for chapter identity over time.

### `1990/1295` — Maaseutuelinkeinolaki

- Run row (`run_20260618T1500`): structural similarity `0.720588`,
  Levenshtein `0.844049`, amendments `18`.
- Current checks: single-statute bench `run_20260618T1506` reproduces
  structural `72.06%`, Levenshtein `84.40%`; `oracle-check` splits the
  remaining 47 divergences across `SOURCE_INCOMPLETE=19`, `REPLAY_MISSING=9`,
  `EDITORIAL_CONVENTION=10`, `MISSING=5`, `ORACLE_STALE=2`, `REPLAY_EXTRA=1`,
  and `UNKNOWN=1`.
- Source witness: `uv run lawvm dump 1990/1295 --after parse --address
  section:51` shows the base source parse has only chapters `1`–`6` and `11`
  (33 sections). Oracle chapters `7`–`10` and sections such as `51`, `53`,
  `56`–`58`, and `60` are absent from the source XML lane; `source-dump
  1990/1295 --address section:51` reports address not found.
- Diagnostics: bench emits
  `SOURCE.ABRIDGED_BASE_CHAPTER_UNRECONSTRUCTABLE×4`,
  `APPLY.FAILED_OPERATION×53`, `unit_missing_left×40`, and many source
  pathologies. Amendments such as `1994/1304` target some later sections
  (`49`, `50`, `52`, `54`, `59a`) but do not reconstruct the missing base
  chapters.
- Status: defer to a base-source acquisition / abridged-base reconstruction
  family. Do not synthesize chapters `7`–`10` or their sections from the
  consolidated oracle alone.

## Unjournaled Worst Levenshtein Queue (2026-06-17 pass 2)

Pass-2 scope: statutes below the already-journaled top band in
`run_20260617T0504`, not yet listed above. Goal is classify/defer, not
benchmaxx. No grammar/regex patches unless a family already has owned tests.

### `1982/91` — Maa-ainesasetus

- Run row: structural similarity `0.909091`, Levenshtein `0.830779`,
  amendments `4`.
- Current check: `uv run lawvm diff 1982/91 --text --threshold 0.999`
  reports `11` compared sections, all perfect; statute score `100.00%`.
- `oracle-check`: `EDITORIAL_CONVENTION=2`, `html-topology:
  missing_from_xml=9 §`.
- Status: comparison-surface/adjudication. Full-text Levenshtein is low because
  bench serializes editorial/oracle-only material that section diff suppresses.
  Not a replay mutation fix.

### `1978/693` — Asetus oikeudesta luovuttaa valtion maaomaisuutta

- Run row: structural similarity `1.000000`, Levenshtein `0.838658`,
  amendments `5`.
- Current check: section-level diff is `93.36%`; only `3 §` diverges at
  `66.8%`.
- Oracle witness: consolidated `3 §` carries both the old minister names
  (`opetusministeriö`, `maa- ja metsätalousministeriö`) and the later
  `ympäristöministeriö` wording in one section body.
- Replay witness: replay has only the later `ympäristöministeriö` text, which
  matches the source-owned amendment outcome.
- `oracle-check`: `ORACLE_STALE=1`.
- Status: oracle-stale duplicate paragraph retention. Do not synthesize stale
  pre-rename text into replay.

### `2019/571` — Laki pankki- ja maksutilien valvontajärjestelmästä

- Run row: structural similarity `1.000000`, Levenshtein `0.843934`,
  amendments `6`.
- Current check: `uv run lawvm diff 2019/571 --text --threshold 0.999`
  reports `33` perfect sections; statute score `100.00%`.
- `oracle-check`: `EDITORIAL_CONVENTION=4`, `html-topology:
  missing_from_xml=7 a §`.
- Status: editorial/oracle projection gap, not replay mutation.

### `2021/617` — Laki hyvinvointialueiden rahoituksesta

- Run row: structural similarity `1.000000`, Levenshtein `0.847348`,
  amendments `7`.
- Current check: section-level score `97.79%`; only `8 luku / 38 §` diverges at
  `7.0%`.
- Source witness: `2022/700` inserts `section:38` with two subsections.
  Subsection `1` carries the operative sunset text plus an inline `<i>Liite</i>`
  marker table; subsection `2` carries the full coefficient annex table.
- Replay witness: `uv run lawvm product-debug 2021/617 --source 2022/700
  --target section:38` materializes both subsections into `38 §` body text.
  Oracle `38 §` keeps only the short operative sentence.
- `oracle-check`: `LIITE_DIFF=1`, `REPLAY_EXTRA=1`.
- Status: annex placement / comparison-surface family. Replay is
  source-faithful to the amendment XML shape; Finlex consolidates the annex
  separately. Needs an owned liite serialization or adjudication rule, not a
  local text delete.

### `1993/1607` — Laki luottolaitostoiminnasta

- Run row: structural similarity `0.928105`, Levenshtein `0.849656`,
  amendments `37`.
- Current check: `uv run lawvm diff 1993/1607 --text --threshold 0.999`
  reports `146` compared sections, `130` perfect, `1` replay-missing, `14`
  replay-extra; statute score `96.59%`.
- Large gaps cluster in chapters `8–9` (`70 §`, `76 §`, `77 §`, `78a §`,
  `79 §`, `80 §`, `81 §`) and `12 luku / 94 §`.
- `oracle-check`: mixed `EDITORIAL_CONVENTION=28`, `EXTRA=10`,
  `ORACLE_STALE=2`, `REPLAY_MISSING=6`, `REPLAY_EXTRA=2`,
  `SOURCE_INCOMPLETE=3`, `UNKNOWN=4`; many `html-topology: missing_from_xml=...`
  rows.
- Root-cause class: multi-wave banking-law recodification plus editorial
  convention and missing-source topology. Not one payload bug.
- Status: defer as dedicated recodification/source-topology family. Do not
  patch individual sections without migration/lineage evidence.

### `1734/4-000` and `1734/3-000` — historical codes

- `1734/4-000`: lev `0.850667`, struct `0.889091`, amendments `198`.
- `1734/3-000`: lev `0.859451`, struct `0.626506`, amendments `25`.
- `1734/3-000` witness: many oracle sections are editorial cross-reference
  stubs (`ks. konkurssisääntö ...`, `ks. MeriL ...`) while replay still carries
  historical operative text or repeal placeholders; `18` replay-extra sections.
- Root-cause class: pre-modern statute editorial projection, repeal-note
  retention, and cross-act reference normalization.
- Status: defer to historical editorial/adjudication family. Not a safe local
  replay patch.

### `2001/1234` — Valtioneuvoston asetus eläinlääkäreiden toimituspalkkioista

- **Status: fixed (universal invariant).**
- Run row before fix: structural similarity `1.000000`, Levenshtein
  `0.851611`, amendments `1`.
- Run row after fix (`run_20260617T1106`): structural `1.000000`, Levenshtein
  `1.000000`.
- Relevant amendment: `2003/811`, `REPLACE section:2/subsection:1/item:h`.
- Source witness: amendment XML carries only table row `H` plus its footnote
  paragraph; base `2001/1234` `2 §` has rows `A`–`H`.
- Failure witness:
  - `uv run lawvm snapshot-debug 2001/1234 --source 2003/811 --target
    section:2` emits whole-section `replace` and subsection `insert` snapshots
    for the row-scoped op.
  - `uv run lawvm product-debug 2001/1234 --source 2003/811 --target section:2`
    materializes only row `H`, dropping base rows `A`–`G`.
- Root-cause class: partial named-table-row replace escalates to whole
  subsection/section snapshot ownership (granularity escalation). Related to
  the named-row table family (`1991/1208`) but here the parser already scopes
  `item:h`; the loss happens in snapshot/materialization, not johtolause
  parsing.
- Deeper witness: `uv run lawvm dump 2001/1234 --address section:2` shows the
  apply fold already collapsed to row-`H` flat text before PIT export — not
  only a late materialization bug. Table rows are not surviving as labelled
  `PARAGRAPH` siblings through apply, so
  `_explicit_subsection_group_snapshot_payload` item-merge path never engages.
- Fix landed: `_prefer_live_fold_section_snapshot_for_descendant_scoped_group`
  now fires only for **item-scoped** groups; blocks sparse `muutos_ir` whole-section
  promotion when live fold is denser. Emits
  `DESTRUCTIVE_SHAPE_LOSS_RISK` /
  `section_snapshot_preserve_live_fold_for_descendant_scoped_item`.
- Regressions:
  - `tests/test_fi_apply.py::
    test_emit_section_snapshot_preserves_live_fold_for_sparse_item_scoped_muutos_shell`
  - `tests/test_fi_materialization_invariants.py::
    TestNoDuplicatesInPIT::test_2001_1234_item_scoped_table_row_snapshot_preserves_sibling_rows`
- Result: `uv run lawvm diff 2001/1234 --text --threshold 0.999` → **100.00%**
  (9/9 sections perfect).

### `1986/508` — Asetus nuorten työntekijäin suojelusta

- Run row: structural similarity `0.571429`, Levenshtein `0.905844`,
  amendments `4`.
- Current check: statute score `88.80%`; worst sections `6 §` (`50.2%`) and
  `2 §` (`77.4%`).
- Ops witness: compiled ledger has only `11` ops from `1990/679`, `1993/1428`,
  and `1997/265`; none target `section:6`.
- Oracle witness: `6 §` and parts of `2 §` carry later consolidated wording
  (expanded poikkeuslupa conditions, longer prohibited-work list) without a
  matching source-owned amendment payload in the replay lane.
- `oracle-check`: `ORACLE_STALE=1`, `REPLAY_MISSING=2`.
- Status: missing-source / carried-baseline frontier. Do not invent `6 §`
  replacement text from oracle alone.

### `1993/1501` — Arvonlisäverolaki

- Run row: structural similarity `0.974419`, Levenshtein `0.877656`,
  amendments `149`.
- Current check: `uv run lawvm diff 1993/1501 --text --threshold 0.999`
  reports `419` compared sections, `362` perfect, `11` replay-missing,
  `12` replay-extra; statute score `98.42%`.
- `oracle-check`: `EDITORIAL_CONVENTION=92`, `MISSING=6`, `ORACLE_STALE=5`,
  `REPLAY_EXTRA=6`, `REPLAY_MISSING=4`, `SOURCE_INCOMPLETE=16`, `UNKNOWN=9`;
  html-topology noncommensurable duplicate labels around `149a`–`149f`.
- Large text gaps include sparse EU VAT directive insertions (`63f §`, `63g §`,
  `72a §`, `209s §`) where replay keeps short source shells and oracle carries
  longer consolidated wording.
- Root-cause class: multi-wave EU VAT recodification, contingent-effective
  sources, temporary-section rebases, and editorial projection. Not one replay
  payload bug.
- Status: defer as dedicated VAT recodification/adjudication family.

### `1994/1472` — Laki nestemäisten polttoaineiden valmisteverosta

- Run row: structural similarity `1.000000`, Levenshtein `0.883586`,
  amendments `43`.
- Current check: section-level score `98.19%`; worst compared section is `2 §`
  at `62.0%` because the definitions list was repeatedly renumbered and
  expanded across amendment waves.
- `oracle-check`: `EDITORIAL_CONVENTION=10`, `LIITE_DIFF=1`,
  `REPLAY_MISSING=1`; html-topology missing sections `6a §`–`14 §` from XML
  lane.
- Smaller gaps (`1 §`, `9 §`) are editorial heading/repeal-notice projection,
  not wholesale structure loss.
- Status: mostly editorial/comparison-surface plus one missing-source topology
  lane. The `2 §` gap is a recodified-definitions family, not a safe local
  text patch.

### `2001/823` — Valtioneuvoston asetus riistanhoitomaksusta ja pyyntilupamaksusta

- Run row: not in top band but queued from pass 2.
- Current check: section-level score `90.01%`; only `2 §` diverges at `70.0%`.
- Source witness: base `2001/823` `2 §` table has four fee rows; `2007/491`
  amendment source carries only two rows (`aikuinen hirvi`, `hirvenvasa`) in a
  whole-section `REPLACE section:2` payload.
- Replay witness: replay keeps the two source-owned rows from `2007/491`.
- Oracle witness: consolidated `2 §` retains four rows with renumbered
  peura-species items `3)` and `4)`.
- `oracle-check`: `REPLAY_MISSING=1`.
- Root-cause class: partial whole-section table replace — source amendment
  omits unchanged sibling rows and expects editorial carry-forward in
  consolidation. Same family as `2001/1234`, but here the source explicitly
  ships a sparse whole-section table payload.
- Status: defer to owned partial-table-replace elaboration policy
  (`_rewrite_partial_whole_section_table_payload` family). Do not invent rows
  `3–4` from oracle alone.

## Pass-2 Verification Notes

- `1987/322` fix confirmed on current code:
  `uv run lawvm bench --statute 1987/322 --no-save` → structural `100.00%`,
  Levenshtein `100.00%`.
- `1979/319` fix confirmed on current code:
  `uv run lawvm bench --statute 1979/319 --no-save` → structural `100.00%`,
  Levenshtein `99.92%`.
- `1929/234` part-move boundary fix confirmed on current code:
  `uv run lawvm bench --statute 1929/234 --no-save` → structural `89.47%`,
  Levenshtein `79.52%`; regression
  `test_replay_xml_1929_234_part_v_rebirth_does_not_repeal_unrelated_part_chapters`
  passes.
- CI gate on touched replay files: `ruff` clean; targeted pytest green;
  full `./scripts/ci.sh --affected` still fails on pre-existing repo-wide
  `ty` diagnostics unrelated to this slice.
- Full-bench verification after `label_reuse_guard` finding ownership
  (`run_20260617T1046` vs user baseline `run_20260617T1035`):
  - Aggregate unchanged: structural **97.37%**, Levenshtein **99.33%**
  - Per-statute diff: **0** changed rows across 3543 OK statutes
  - `1929/234` unchanged: structural `89.47%`, Levenshtein `79.52%`
  - New diagnostics only: `1929/234` now reports `APPLY.MOVE_SKIP×5` (guard
    suppressions); scores unaffected
- Full-bench after `2001/1234` item-scoped snapshot fix (`run_20260617T1058` vs
  `T1046`): structural **97.37%**, Levenshtein **99.34%**; only
  `2001/1234` moved (`0.851611` → `1.000000` lev).
- Full-bench after `1929/234` part-insert recovery (`run_20260617T1106` vs
  `T1058`): structural **97.37%**, Levenshtein **99.35%**; only `1929/234`
  moved (lev `0.801350` → `0.821092`; struct `0.895425` → `0.888158` — extra
  replay-owned sections vs oracle stubs).
- Full-bench after `1868/31-000` §83 letter-list apply (`run_20260617T1132` vs
  `T1106`): aggregate unchanged structural **97.37%**, Levenshtein **99.34%**;
  only `1868/31-000` moved (lev `0.606019` → `0.692415`; struct `0.905172` →
  `0.913793`). `1991/1208` unchanged (`ELAB.NAMED_ROW_PROVINCE_TABLE_MERGE×2`
  still present).
- Single-statute after `2017/320` relabel migration ledger lookup
  (`run_20260617T1157`): structural **83.55%** (was `77.14%`), Levenshtein
  **58.62%** (was `60.72%`); `APPLY.RELABEL_MIGRATION_LEDGER_LOOKUP×10`;
  `part:3/chapter:1`/`chapter:2` relabel skips cleared.
- After structural label alias (`run_20260617T1206` + `diff`): section score
  **90.56%**; `APPLY.RELABEL_STRUCTURAL_LABEL_ALIAS_LOOKUP×1`; `part:2a`
  relabel executes.
- After post-apply dedup (`run_20260617T1223` + `diff`): section score
  **90.62%**; `APPLY.GLOBAL_LABEL_DEDUP_APPLIED×3` (includes
  `process_muutoslaki.post_apply` for `2019/371`); `diagnose-phase` duplicate_label
  all clean; `APPLY.TREE_INVARIANT_VIOLATION` **57→1**. Regressions:
  `test_process_muutoslaki_2017_320_2019_371_post_apply_dedup_*`,
  `TestExecuteRelabel` 25/25.

## Pass-3 Shallow Queue (structural 1.0 band, lev ≈ 0.86–0.91)

Quick `diff --text` + `oracle-check` on the next unjournaled worst rows from
`run_20260617T0504`. All classified as comparison-surface / oracle-stale, not
replay mutation fixes:

| Statute | Section diff | oracle-check |
|---------|--------------|--------------|
| `2006/386` | 10/10 perfect, 3 replay-extra | `ORACLE_STALE=3` |
| `1993/81` | 0/4 perfect (wording only) | mixed editorial/extra/missing |
| `2011/516` | 1/2 perfect | `REPLAY_MISSING=1` — **missing-source**: 0 compiled ops; `1 §` effective-date/phrasing differs from oracle (`6.6.2011 alkaen` + OK reference); defer |
| `1986/919` | 2/3 perfect | `EDITORIAL_CONVENTION=1`, `ORACLE_STALE=1` |
| `1994/1070` | 24/26 perfect | `EDITORIAL_CONVENTION=3`, `ORACLE_STALE=2` |
| `2020/53` | 6/7 perfect | `EDITORIAL_CONVENTION=1`, `REPLAY_EXTRA=1` |
| `2006/79` | 5/5 perfect, 1 replay-extra | `ORACLE_STALE=1` |
| `2001/1170` | 5/6 perfect | `ORACLE_STALE=1` |
| `2016/264` | 2/4 perfect | `ORACLE_STALE=2` |

### `1993/1709`

- Run row: structural similarity `0.666667`, Levenshtein `0.912341`,
  amendments `6`.
- `oracle-check`: `REPLAY_MISSING=1` only.
- Status: low structural similarity with a single missing section — likely
  missing-source or address migration, not a full-text serialization bug.
  Queue for dedicated pass if it re-enters the top band after refresh.

#### 2026-06-19 refresh

- Current row (`run_20260619T0815`): structural `0.666667`,
  Levenshtein `0.605544`, amendments `6`.
- Commands:
  - `uv run lawvm diff 1993/1709 --text --threshold 1.0 --compile-summary`
    reports `3 compared`, `2 perfect`, and `1 §` at `58.6%`.
  - `uv run lawvm oracle-check 1993/1709` reports `64.3%` over `1`
    diverging section, `REPLAY_MISSING=1`, source pathology
    `EMPTY_OPERATIVE_BODY`.
  - `uv run lawvm inspect-amendment 1993/1709 --source 1996/704 --stage all`
    shows one compiled op: `REPLACE 1 §`.
  - `uv run lawvm source-dump 1996/704 --address section:1` shows a direct
    leading omission, then a single subsection whose intro is
    `Psykotrooppisia aineita koskevan yleissopimuksen luettelo I`.
- Source witness: the `1996/704` preamble says it changes the lists I-IV of
  the psychotropic-substances convention contained in `1 §`
  (`1 §:ään sisältyvän ... luetteloita I, II, III ja IV seuraavasti`), not the
  whole section.
- Root cause: current base/source IR has no typed boundary for convention-list
  blocks inside the single giant `1 §` subsection. The base `1993/1709` §1
  stores the 1961 narcotics convention lists and the psychotropic convention
  lists as one subsection. The amendment body carries only the psychotropic
  tail after an omission. Compiling this as whole-section `REPLACE 1 §` deletes
  the earlier 1961-list prefix, causing the huge Levenshtein gap.
- Disposition: **defer as typed list-block segmentation frontier**. A correct
  fix should introduce an owned legal-list block representation or an explicit
  source-normalization rule for in-subsection list headings, then lower the
  `sisältyvän ... luetteloita` amendment to replacement of those list blocks.
  Do not patch this with a one-off substring splice or random johtolause regex;
  the grammar already found the statute/section and the missing concept is
  structure below subsection level.

## Remaining Work Queue

Ordered by lowest current Levenshtein among statutes not marked fixed/deferred
above:

1. `1991/1208` — attachment/PDF payload lane (already deferred twice; keep
   deferred).
2. `2017/320` — **migration pass in progress** — relabel ledger lookup,
   `part:2a` alias, and post-apply dedup landed; next: omission-only shells
   (`209§`–`215§`, `SOURCE_INCOMPLETE`).
3. `1868/31-000` — **journaled/deferred** after §85 overlay + §83 letter-list
   fixes; residuals are oracle-stale/editorial/source-incomplete.
4. `1992/1535` — comparison-surface/adjudication.
5. Partial-table-row family: `2001/823` (sparse whole-section table defer),
   `1991/1208` (parser/attachment). `2001/1234` fixed.
6. Pass-2/3 deferred: `1982/91`, `1978/693`, `2019/571`, `2021/617`,
   `1993/1607`, `1734/*`, `1986/508`, `1993/1501`, `1994/1472`, pass-3
   structural-1.0 band (`2006/386`, `1993/81`, …).
7. `1929/234` — part-move boundary + part-insert uncovered-body fixes landed;
   residual editorial/extra topology only.
8. `1993/1709` — shallow-triaged; single `REPLAY_MISSING`, revisit if promoted.

## 2026-06-18 Refresh

Baseline run: `data/bench_runs/20260618T1830_run_20260618T1830.csv`
after commits:

- `a98ac573` — extracted FI audit/proof-surface helpers.
- `b506dfa0` — comparison-only FI office-schedule grouping detector.

Aggregate: structural **97.45%**, Levenshtein **99.41%**. Full comparison
against `20260618T1755`: **0 regressions**, **28 improvements**. The largest
movement is `1998/132` structural `0.500000 -> 1.000000` with identical
Levenshtein, by classifying Finlex grouped office schedule rows vs LawVM split
rows as presentation-only comparison drift. This did not change replay output.

Current worst Levenshtein band and disposition:

| Rank | Statute | Lev | Structural | Disposition |
|------|---------|-----|------------|-------------|
| 1 | `1991/1208` | `0.598139` | `0.920000` | Already journaled: named-row province table merge partly fixed; residual attachment/PDF payload lane (`2001/995`+) is manual/source frontier. |
| 2 | `2017/320` | `0.642791` | `0.807504` | Already journaled: migration/relabel family; remaining same-wave omission shells and source pathologies need a dedicated migration pass, not random grammar/regex changes. |
| 3 | `1868/31-000` | `0.666451` | `0.895652` | Already journaled/deferred after §85 overlay + §83 letter-list fixes; residuals are oracle/editorial/source-incomplete. |
| 4 | `1982/182` | `0.542737` | `0.548387` | Re-triaged again on `run_20260619T0310`: source-incomplete base chapter 4 plus image-only traffic-sign payloads; no replay text injection. |
| 5 | `1992/1535` | `0.728264` | `0.908213` | Already journaled: comparison/adjudication and large tax-law recodification family. |
| 6 | `1999/329` | `0.829504` | `0.960000` | Already journaled; source-pathology/uncovered-body family, revisit only with bounded source witness. |
| 7 | `1978/693` | `0.838658` | `1.000000` | Already journaled as comparison/oracle-stale band; no replay mutation queued. |

New 2026-06-18 skip note:

### `2009/1182` — Opetus- ja kulttuuriministeriön suoritteiden maksullisuus

- Current row: structural `0.500000`, Levenshtein `0.983900` (`lev_similarity`
  `0.983900` in the refreshed full run; single-statute check reports
  Levenshtein error `1.61%`).
- Diagnostics: text-only `wording_text_changed`/`intro_text_changed`,
  `ELAB.STRICT_REJECTED_OPERATION`, `REPLAY.MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT`,
  and `source_adjudication:oracle_suspect`.
- Witness:
  - `lawvm structural-review 2009/1182 --dump` shows only sections `1 §` and
    `3 §` text wording differences (`opetusministeriö` vs
    `opetus- ja kulttuuriministeriö`, plus item `5` wording).
  - `lawvm explain 2009/1182` attributes both diverging sections to
    `2011/250`, classifies both as `EDITORIAL_CONVENTION`, and shows the
    temporary expiry surface.
  - `lawvm oracle-check 2009/1182` reports `EDITORIAL_CONVENTION=2` and
    `LIITE_DIFF=1`.
- Disposition: **skip for replay**. This is comparison/adjudication plus
  attachment/liite projection, not a source-owned replay repair. Do not change
  johtolause parsing or replay text to match the stale oracle wording.

### `1982/182` — Tieliikenneasetus refresh

- Current row: structural `0.548387`, Levenshtein `0.687281`; single-statute
  check reports structural error `45.16%`, Levenshtein error `31.27%`.
- Diagnostics: `unit_missing_left×270`, `unit_missing_right×214`,
  `wording_text_changed×57`, `facet_added×42`, product/tree invariant warnings,
  fallback whole-section replacement, uncovered-body recovery, sparse slot
  leftovers, source pathology, and source-incomplete findings.
- `lawvm oracle-check 1982/182` reports `68.5%` over `34` diverging sections:
  `EDITORIAL_CONVENTION=13`, `SOURCE_INCOMPLETE=9`, `REPLAY_MISSING=5`,
  `MISSING=4`, `ORACLE_STALE=2`, `REPLAY_EXTRA=1`, with
  `DESTRUCTIVE_SHAPE_LOSS_RISK`.
- Structural-review hot spots are traffic-sign catalog sections, especially
  `13 §`, `16 §`, `19 §`, `20 §`, and `21 §`. LawVM often carries flat
  sequential moment text for signs while Finlex presents nested catalog
  paragraphs/items/subitems plus editorial stubs/repeal notices.
- Phase-local conclusion: not a clean broad apply/replay bug. Sampled
  diagnose-phase paths stay internally coherent; the dominant gap is
  payload/elaboration/source-pathology/comparison topology for image/table-like
  traffic-sign catalogs.
- Action taken: fixed the diagnostic tool crash where `lawvm explain 1982/182
  --section '21 §'`/`'40 §'` could slice `source_title=None`. This is tooling
  robustness only; replay semantics unchanged.
- 2026-06-19 refresh: latest full bench row is structural `0.548387`,
  Levenshtein `0.542737`; `oracle-check` now reports `54.2%` over `35`
  diverging sections. The new worst status is explained by the same source
  frontier, not by a new resolver regression.
- Additional witness:
  - `lawvm ops 1982/182 --target section:27` shows the expected
    `1990/934 REPLACE section:27`.
  - The `1990/934` body has `27 §` with the intro paragraph and then only an
    image block (`media/0263.gif`), while the oracle has three numbered
    text items for the light meanings.
  - `lawvm ops 1982/182 --target section:{24,25,29,30}` shows no operations,
    and the original `1982/182` XML has chapters 1-3 and 8-9 but no chapter 4.
    The four missing chapter 4 sections are inherited-base source gaps.
- Disposition: **defer broad replay work** until there is a named
  traffic-sign/catalog payload elaboration family with source witnesses,
  strict-mode barriers for image-only gaps, synthetic tests, and negative tests
  proving flat sign-list text is not silently converted into oracle-only nested
  structure.

Next pass should move to `2017/320` only for a bounded migration/relabel pass,
or otherwise continue down the refreshed worst-Levenshtein list with the same
fix-or-journal discipline.

### `1966/618` — Evankelis-luterilaisen kirkon eläkeasetus

- Current row: structural `0.833333`, Levenshtein `0.914862`; single-statute
  check reports structural error `16.67%`, Levenshtein error `8.51%`.
- `lawvm oracle-check 1966/618` reports `EDITORIAL_CONVENTION=4` and
  `ORACLE_STALE=1`, with `DESTRUCTIVE_SHAPE_LOSS_RISK` and HTML topology gaps
  for `1 §`, `5 §`, and `7 §`.
- Structural-review visible gaps:
  - `2 §` has Finlex intro + `a`/`b` item topology while LawVM carries the
    corresponding text flat, but `lawvm explain` classifies the section as
    `ORACLE_STALE` against the pre-`1981/68` state.
  - `3 §` is spacing/editorial repeal notice surface (`kumottu A:lla
    8.2.1985/146`).
- Disposition: **skip for replay**. This is stale/editorial/oracle topology,
  not source-owned authority to restructure current replay from oracle text.

### `1986/244` — Asetus tieliikennettä koskevan yleissopimuksen voimaansaattamisesta

- Current row: structural `1.000000`, Levenshtein `0.915781`; single-statute
  check is structurally perfect and reports Levenshtein error `8.42%`.
- `lawvm oracle-check 1986/244` reports `91.6%` and **no section
  divergences**; `lawvm explain 1986/244 --threshold 0.999` says all sections
  are at or above threshold.
- Disposition: **skip for replay**. The residual Levenshtein gap is outside
  section-level replay comparison, likely treaty/attachment/wrapper projection,
  and there is no replay-owned section divergence to fix.

### `1999/211` — Opetusministeriön päätös opetushallituksen suoritteiden maksullisuudesta

- Current row: structural `1.000000`, Levenshtein `0.917919`; single-statute
  check is structurally perfect and reports Levenshtein error `8.21%`.
- `lawvm oracle-check 1999/211` reports `100.0%` and no divergences;
  `lawvm explain 1999/211 --threshold 0.999` says all sections are at or above
  threshold.
- Disposition: **skip for replay**. No section-level replay divergence exists;
  residual Levenshtein is outside the actionable legal-section surface.

### `1996/1093` — Metsälaki

- Current row: structural `0.953488`, Levenshtein `0.918243`; single-statute
  check reports structural error `4.65%`, Levenshtein error `8.18%`.
- `lawvm oracle-check 1996/1093` reports five divergences, all
  `EDITORIAL_CONVENTION`, with `DESTRUCTIVE_SHAPE_LOSS_RISK` and HTML topology
  gaps for `4 §`, `14 a §`, and `17 §`.
- Structural-review visible gaps:
  - `7 a §`: `Natura-2000` vs `Natura 2000 -` wording/punctuation surface.
  - `18 §`: wrap-up text in LawVM vs Finlex nested under item `5` as a
    subitem-like projection, plus editorial date in heading.
- Disposition: **skip for replay**. These are comparison/editorial projection
  differences, not source-owned legal-state repairs.

### `1968/360` — Laki elinkeinotulon verottamisesta

- Current refreshed baseline row (`20260618T1830`): structural `0.859813`,
  Levenshtein `0.919478`.
- Root cause fixed in this pass: amendment `2019/308` has a leading
  `III osan 3 luvun otsikko` target and later starts a new `lisätään` group.
  The old chapter-chunk assignment leaked that chapter-3 context into bare
  `1 §:ään` and `2 §:ään` subsection inserts, producing false targets
  `chapter:3/section:1/subsection:2` and
  `chapter:3/section:2/subsection:2`.
- Source witness:
  - The johtolause says `lisätään 1 §:ään ... uusi 2 momentti, 2 §:ään ...
    uusi 2 momentti`; it does not explicitly cite chapter 3 for these targets.
  - The amendment body places `1 §` and `2 §` as flat sections, while later
    `42 a §`, `51 d §`, and `53 §` are under `III OSA / 3 luku`.
  - The pre-`2019/308` live state resolves `1 §` and `2 §` uniquely under
    `part:1` with no chapter.
- Fix shape: target-binding guard in `assign_chapter_scope_from_johtolause`
  skips assigning a chapter chunk to subsection/facet/item INSERT targets when
  the source did not explicitly bind `chapter+section` and the live target is a
  unique unchaptered section. This leaves proper nearby chapter-scoped targets
  intact (`7 §` -> chapter 2; `42a §`, `51d §`, `53 §` -> chapter 3).
- Verification:
  - `lawvm ops 1968/360 --source 2019/308` now emits
    `INSERT section:1/subsection:2` and `INSERT section:2/subsection:2`.
  - Focused bench: structural `0.857143`, Levenshtein `0.926255` (Levenshtein
    improves; structural percentage shifts slightly because the false
    chapter-3 nodes are removed and the remaining residual denominator changes).
  - `lawvm oracle-check 1968/360` improved from 33 diverging sections to 29 in
    the first fixed run; remaining families are editorial/source-pathology and
    known temporary/topology residuals.
  - Full bench `20260618T1920` retained aggregate structural `97.45%` and
    Levenshtein `99.41%`. Row-level comparison against `20260618T1830` showed
    the intended `1968/360` tradeoff and a tiny Levenshtein-only movement in
    `1962/420` (`-0.000266`, structural unchanged), investigated as an
    unchanged editorial/text-only `8 §` residual around `2024/247`.
- Disposition: **fixed** for the chapter-context leak family; do not broaden
  this into global chunk truncation, because a broader attempt regressed
  `1993/1607`/`2014/917` before being narrowed.

### `2014/120` — Valtioneuvoston asetus julkisen talouden suunnitelmasta

- Current baseline row (`20260618T1929`): structural `0.857143`,
  Levenshtein `0.922464`.
- Root cause fixed in this pass: amendment `2017/601` says
  `lisätään 3 §:ään ... uusi 8−10 momentti ...`, where the range dash is
  U+2212 mathematical minus. `_DASH_CLASS` already accepted the glyph in
  compound range regexes after the local edit, but standalone dash
  classification still used a separate hard-coded dash set and tokenized
  `8−10` as `NUM WORD NUM`. The parser therefore fell back to the partial
  `muutetaan` targets and missed explicit inserts for `3 §` subsections
  `8`, `9`, and `10`.
- Source witness:
  - `lawvm ops 2014/120 --source 2017/601` before the fix emitted only
    `REPLACE section:3/subsection:2`, `REPLACE section:3/subsection:4`,
    `REPLACE section:4`, and `INSERT section:5a`.
  - The source XML contains three added sparse payload slots after the two
    replacement subsections, matching the explicit `8−10 momentti` range.
- Fix shape: include U+2212 in the lexicon-owned dash class and use a single
  `_DASH_ONLY_RE` for standalone dash token classification. This is lexical
  source glyph normalization, not target fallback or sparse-payload execution.
- Verification:
  - Focused parse/compile tests cover the U+2212 range and the real
    `2014/120 <- 2017/601` source.
  - `lawvm ops 2014/120 --source 2017/601` now emits
    `INSERT section:3/subsection:8`, `9`, and `10`, plus `INSERT section:5a`.
  - Single-statute bench `20260618T1942`: structural `1.000000`,
    Levenshtein `1.000000`.
  - Full bench `20260618T1942` vs `20260618T1929`: five rows improved
    (`1997/210`, `2005/251`, `2011/1365`, `2014/120`, `2016/658`), zero
    regressions. Aggregate structural accuracy rose to `97.47%`; Levenshtein
    remained `99.41%`.
- Disposition: **fixed** for FI johtolause dash-glyph range parsing.

### `1734/3-000` — Kauppakaari

- Baseline row (`20260618T1942`): structural `0.626506`, Levenshtein
  `0.859451`.
- Fixed row (`20260618T2025`): structural `0.698795`, Levenshtein `0.779427`.
- Root cause fixed in this pass: amendment `1973/390` contains a chapter
  destination followed by descriptive provenance before the actual
  reinstatement payload:
  `lisätään 9 lukuun määräajasta ... asetuksella kumotun 12 §:n sijaan uusi
  12 § sekä uusi 13 §`. The parser treated the provenance as a boundary and
  missed the explicit chapter-9 section inserts.
- Source witness:
  - Before the fix, parsing the full johtolause emitted only the chapter-12
    replacement and missed `chapter:9/section:12` and `chapter:9/section:13`.
  - After the fix, `inspect-amendment 1734/3-000 --source 1973/390` emits the
    chapter-12 replacement plus the two chapter-9 section inserts, with payloads
    bound from the source body.
- Core materialization issue exposed by the parser fix: selected section
  snapshots that carry subsection payloads were being erased by older inactive
  descendant timelines during PIT overlay. The materializer now lets a selected
  section snapshot mask an inactive subsection/item tombstone only when the
  child expiry is older than the section snapshot. A later child expiry remains
  an explicit tombstone and wins.
- Guard cases:
  - `1997/1363` keeps the recovered section-55 payload from `2001/1466`.
  - `1996/580` does not resurrect expired subsection `4 a §(7)`.
  - Chapter/part-level inactive-section tombstones are not masked by this rule;
    the earlier broad version resurrected stale old-location sections in
    `1997/354` and `2021/1289`.
- Verification:
  - Focused parser/compile/materialization tests pass.
  - Ruff passes for changed files.
  - Full bench `20260618T2025` vs committed baseline `20260618T1942`: changed
    rows are `1734/3-000` (structural up, Levenshtein down), `1997/1363`
    (Levenshtein up, structural unchanged), and `1961/264` (tiny structural
    proxy down, Levenshtein up). No both-metric regressions.
  - `1961/264` structural-review comparison showed the visible review surface
    removing extra LawVM-only chapter-12 sections; the tiny bench structural
    proxy movement is not treated as a legal-state regression.
- Disposition: **fixed** for chapter-destination reinstatement preambles and
  selected-section-over-inactive-descendant materialization. Residual
  `1734/3-000` Levenshtein drop is accepted as a source-faithful topology move
  against an old-law oracle surface with noncommensurable residuals.

### `2006/386` — Maa- ja metsätalousministeriön lintuinfluenssavarotoimiasetus

- Current row (`20260618T2025`): structural `1.000000`, Levenshtein
  `0.869967`; single-statute check reports Levenshtein error `13.00%`.
- `lawvm oracle-check 2006/386` reports `ORACLE_STALE=3` and no diverging
  sections in the section summary.
- Structural-review visible gaps are LawVM-only sections `5 a §`, `5 b §`,
  and `5 c §` under chapter 3.
- Source witness/phase conclusion:
  - The extra sections come from repeated fixed-date seasonal amendments
    inserting `4 a–4 c §`/`5 a–5 c §` with explicit expiry windows.
  - The row is dominated by temporary/official-consolidation selection and
    stale-oracle comparison, not by a missing source operation or malformed
    target resolution.
- Disposition: **skip for replay**. Do not delete temporary replay state merely
  to match the cached oracle surface; this belongs to oracle/PIT selection and
  temporary-section adjudication.

### `1992/1597` — Laki Euroopan talousalueen valtioiden kansalaisten tutkintotodistusten tunnustamisesta

- Baseline row (`20260618T2120`): structural `0.600000`, Levenshtein
  `0.927590`.
- Root cause fixed in this pass: amendment `1997/419` was being routed as
  `citation_mismatch_skip` because the preamble starts by repealing the prior
  amending act `(579/1994)` and only then continues with base-statute
  operations (`muutetaan lain nimike, 1 ja 2 §, ...`). The repealed
  amending-act citation was treated as the target citation for the whole
  preamble.
- Source witness:
  - `lawvm inspect-amendment 1992/1597 --source 1997/419 --json --stage source`
    previously reported `should_apply=false`, `reason=citation_mismatch_skip`,
    and compiled zero operations.
  - `lawvm source-dump 1994/579` shows the 1994 title replacement to
    `Euroopan talousalueen valtioiden kansalaisten koulutuksen ja
    ammatillisen harjoittelun tunnustamisesta`.
  - `lawvm source-dump 1997/419` shows a full base-statute replacement wave,
    including the new docTitle and sections `1`, `2`, `3`, `4`, `5`, `6`,
    `7`, `8`, `9`, `12`, and `13`.
- Fix:
  - `citation_routing` now recognizes the named routing family
    `leading_meta_repeal_then_parent_ops`, restricted to single-target
    amending-act titles and rejected when the post-repeal operative text cites
    another target statute.
  - The route reason is surfaced as an apply reason, not collapsed into a
    silent `references_parent` success.
- Verification:
  - `pytest tests/test_fi_citation_routing.py -q`: `40 passed`.
  - `lawvm inspect-amendment 1992/1597 --source 1997/419 --json --stage source`
    now reports `should_apply=true`,
    `reason=leading_meta_repeal_then_parent_ops`, and compiles the 1997
    replacement wave.
  - Single-statute bench: structural `0.600000 -> 0.733333`, Levenshtein
    `0.927590 -> 0.993876`.
  - Full bench comparison (`20260618T2120_run_20260618T2120.csv` vs
    `20260618T2153_candidate_leading_meta_repeal_route.csv`) shows exactly one
    changed row, `1992/1597`, and no regressions.
- Residual: structural accuracy is still below perfect because title/nimike and
  sparse subsection residuals remain separate issues. The high-value route gap
  is fixed without synthesizing title support or broadening numeric citation
  routing.

### `2005/653` — Valtioneuvoston asetus rajavyöhykkeestä ja rajavyöhykkeen takarajasta

- Current row (`20260618T2120`): structural `1.000000`, Levenshtein
  `0.923524`; single-statute check reports Levenshtein error `7.65%`.
- `lawvm diff 2005/653 --text --threshold 0.999` reports eight compared
  sections, seven perfect, and one divergent section (`6 §`) at about `91%`.
- `lawvm oracle-check 2005/653` reports a single diverging section and
  classifies it as `EDITORIAL_CONVENTION`, with source pathologies
  `DESTRUCTIVE_SHAPE_LOSS_RISK`, `ITEM_TARGET_STRUCTURE_ABSENT`, and
  `SPARSE_ITEM_BODY_MISSING`.
- Source witness:
  - `lawvm replay-plan 2005/653` selects
    `finlex://sd-cons/2005/653/fin@20220416/main.xml`, whose version act
    `2022/416` is future-effective (`2022-06-15`) relative to the XML cutoff
    (`2022-06-09`).
  - `lawvm ops 2005/653 --source 2022/416` emits the source-owned operations:
    `REPLACE section:5` and item replacements for `6 §` items `7`, `9`, `12`,
    `13`, `14`, `16`, `19`, `21`, and `22`. It does not authorize a whole
    section-6 coordinate catalog rebuild.
  - `lawvm source-dump 2022/416 --address section:6` shows a sparse section
    body with omission markers and only those changed item blocks.
- Visible residual: Finlex presents §6 as a numbered coordinate catalog with
  editorial repeal-note topology for omitted/repealed items, while LawVM
  preserves the source-owned live item labels and sparse replacement history.
  Structural-review therefore aligns later municipality items by ordinal and
  emits many item-intro/text differences, even though section-level structure is
  perfect and the only authoritative payload in `2022/416` is sparse.
- Disposition: **skip for replay**. This is comparison/editorial projection for
  a sparse coordinate-catalog section plus future-effective oracle-version
  presentation, not authority to synthesize a complete renumbered §6 from the
  oracle. Revisit only with an owned sparse-catalog comparison/adjudication
  family, not a johtolause/parser regex change.

### `1998/631` — Laki ammatillisesta aikuiskoulutuksesta

- Current row (`20260618T2120`): structural `1.000000`, Levenshtein
  `0.923917`.
- `lawvm diff 1998/631 --text --threshold 0.999` reports forty-eight compared
  sections, forty-five perfect, and only near-perfect text differences in
  sections such as `10 §`, `11 §`, and `16 §`.
- `lawvm oracle-check 1998/631` reports six diverging sections, all classified
  as `EDITORIAL_CONVENTION`, with `DESTRUCTIVE_SHAPE_LOSS_RISK`.
- `lawvm structural-review 1998/631 --dump` emits no structural events on the
  current tree, which supports the section-level structural-perfect row.
- Source/oracle witness:
  - `lawvm replay-plan 1998/631` selects
    `finlex://sd-cons/1998/631/fin@20160507/main.xml`, whose version act
    `2016/507` is future-effective (`2016-08-01`) relative to the XML cutoff
    (`2016-06-29`).
  - The visible text gaps are Finlex editorial annotations and repealed-item
    display text: e.g. `10 §` includes `(3.10.2014/788)` and
    `6 kohta on kumottu L:lla 29.6.2016/507`; `11 §` shows `6–7 kohdat` and
    `20 kohta` repeal notes; `16 §` shows `6 a kohta on kumottu L:lla
    4.9.2015/1111`.
- Disposition: **skip for replay**. The residual is item-level editorial
  repeal-note/date projection in the official consolidation surface, not a
  missing source-owned legal mutation. Do not alter replay state or parser
  behavior to inject these display notes.

### `2022/375` — Sosiaali- ja terveysministeriön asetus lääkäri- ja hammaslääkärikoulutuksen korvauksiin ja yliopistotasoisen terveyden tutkimuksen rahoitukseen oikeutetuista palvelujen tuottajista

- Current row (`20260618T2120`): structural `1.000000`, Levenshtein
  `0.924231`; single-statute check reports Levenshtein error `7.58%`.
- `lawvm diff 2022/375 --text --threshold 0.999` reports three compared
  sections, two perfect, and one missing-oracle paragraph in `3 §`.
- `lawvm oracle-check 2022/375` classifies the single divergence as
  `ORACLE_STALE`.
- Source witness:
  - `lawvm replay-plan 2022/375` selects
    `finlex://sd-cons/2022/375/fin@20220834/main.xml`, whose version act
    `2022/834` is future-effective (`2022-09-30`) relative to the XML cutoff
    (`2022-09-23`).
  - `lawvm ops 2022/375 --source 2022/834` emits one source-owned operation:
    `REPLACE section:3`.
  - `lawvm source-dump 2022/834 --address section:3` shows only the sparse
    replacement body for `3 §`: the heading, omission marker, and the paragraph
    about applying `1 §` to the first half of 2022. The consolidated oracle's
    extra paragraph repealing `1125/2013` is not present in that amendment
    section body.
  - `lawvm inspect-amendment 2022/375 --source 2022/834` shows the prepared
    payload preserves the original first commencement paragraph and adds the
    source-owned second paragraph; it does not have source authority for the
    oracle-only repeal paragraph.
- Disposition: **skip for replay**. This is a source-incomplete/oracle-stale
  commencement display gap in the cached consolidation, not a lawful basis to
  synthesize an extra `3 §` subsection from the oracle.

### `2011/516` — Oikeusministeriön asetus ulosottoperustetta koskevan tuomioistuimen ilmoitusvelvollisuuden alkamisesta

- Baseline row (`20260618T2025`): structural `1.000000`, Levenshtein
  `0.875233`; single-statute check reported Levenshtein error `12.48%`.
- Root cause fixed in this pass: amendment `2011/582` has a short but complete
  operative preamble, `muutetaan (516/2011) 1 § seuraavasti:`. The acquisition
  layer treated any johtolause under 50 characters as too short and selected
  the section-1 body text as a pre-routing fallback. That fallback is payload,
  not an operative formula, so no operation compiled.
- Source witness:
  - `source-dump 2011/582` shows section 1 split into two subsections.
  - `oracle-text 2011/516 --section section:1` shows the same replacement text
    after oracle version `2011/582`.
  - Before the fix, `ops 2011/516 --source 2011/582` emitted zero operations
    and `inspect-amendment` showed `Sec1 fallback: yes`.
  - After the fix, `inspect-amendment 2011/516 --source 2011/582` shows
    `Sec1 fallback: no` and `REPLACE 1 §`.
- Fix shape: `should_use_sec1_fallback_pre_routing` no longer discards a
  primary preamble solely because it is short when it contains an explicit
  Finnish operation keyword. A bench-chain scan found exactly one affected
  short-operative-preamble edge: `2011/582 -> 2011/516`.
- Verification:
  - Focused acquisition and compile tests pass.
  - `oracle-check 2011/516` reports `100.0%` and no divergences.
  - Single-statute bench `20260618T2036`: structural `1.000000`,
    Levenshtein `1.000000`.
- Disposition: **fixed** for short operative preambles that were incorrectly
  overridden by section-1 fallback payload.

### `1986/919` — Oikeusministeriön päätös vankeusrangaistuksen täytäntöönpanosta eräissä tapauksissa

- Current row (`20260618T2025`): structural `1.000000`, Levenshtein
  `0.878040`; single-statute check reports Levenshtein error `12.20%`.
- `lawvm oracle-check 1986/919` reports `EDITORIAL_CONVENTION=1` and
  `ORACLE_STALE=1`.
- Structural-review visible gap: oracle section `1 §` has itemized `a–e`
  topology that LawVM does not show at the selected oracle cutoff.
- Source witness/phase conclusion:
  - `explain 1986/919 --threshold 0.999` attributes `1 §` to `1991/681` and
    classifies it as `ORACLE_STALE`: replay materializes a future-dated version
    beyond the oracle cutoff.
  - `2 §` is classified as `EDITORIAL_CONVENTION`.
- Disposition: **skip for replay**. This is an oracle-cutoff/editorial surface
  issue, not authority to change replay target parsing or payload structure.

### `1994/1070` — Laki tuontipolttoaineiden velvoitevarastoinnista

- Current row (`20260618T2039`): structural `1.000000`, Levenshtein
  `0.896968`; single-statute check reports Levenshtein error `10.30%`.
- `lawvm oracle-check 1994/1070` reports `EDITORIAL_CONVENTION=3` and
  `ORACLE_STALE=2`, with HTML topology mismatch (`missing_from_xml=12 §,18 §`,
  `extra_in_xml=4 luku / 13a §,5 luku / 16a §`).
- Structural-review visible gaps are Finlex-only subsections under `13 a §`
  and `16 a §`.
- Disposition: **skip for replay**. The row is stale/editorial/topology
  comparison noise, not a source-owned replay operation to add.

### `2020/53` — Valtioneuvoston asetus Digi- ja väestötietovirastosta

- Current row (`20260618T2039`): structural `1.000000`, Levenshtein
  `0.898670`; single-statute check reports Levenshtein error `10.13%`.
- `lawvm oracle-check 2020/53` reports `EDITORIAL_CONVENTION=1` and
  `REPLAY_EXTRA=1`; structural-review shows section `7 §` text in LawVM while
  Finlex shows the editorial notice `7 § on kumoutunut, ks. L 746/2024`.
- Source witness:
  - `replay-plan 2020/53` includes only `2025/1116`; it is oracle-suspect
    because its effective date is `2026-01-01` after the cached oracle cutoff.
  - `inspect-amendment 2020/53 --source 2024/746` correctly skips by
    `citation_mismatch_skip`: `2024/746` amends the law `2019/304`, not the
    decree `2020/53`.
  - `source-dump 2024/746` confirms the source target is `2 §` of
    `Digi- ja väestötietovirastosta annettu laki (304/2019)`.
- Disposition: **defer**. This is delegated-authority/supersession projection
  from an amendment to the parent law into a subordinate decree, not a direct
  amendment operation against `2020/53`. Do not synthesize a repeal without a
  named cross-instrument supersession/authority rule and evidence contract.

### `2006/79` — Valtioneuvoston asetus aravavuokratalojen purkamiskustannuksiin myönnettävästä avustuksesta

- Current row (`20260618T2039`): structural `1.000000`, Levenshtein
  `0.900000`; single-statute check reports Levenshtein error `10.00%`.
- `lawvm oracle-check 2006/79` reports `ORACLE_STALE=1`.
- Structural-review visible gap is LawVM-only `4 a §`, a temporary/future
  state surface absent from the cached oracle.
- Disposition: **skip for replay**. This is stale-oracle/PIT selection, not a
  source-owned target-resolution or payload bug.

### `2001/1170` — Valtioneuvoston asetus eräistä raha-automaattiavustuksiin sovellettavista määräajoista

- Current row (`20260618T2039`): structural `1.000000`, Levenshtein
  `0.908294`; single-statute check reports Levenshtein error `9.17%`.
- `lawvm oracle-check 2001/1170` reports `ORACLE_STALE=1`.
- Structural-review visible gap is LawVM-only `2 §(3)`, a stale-oracle/PIT
  selection mismatch.
- Disposition: **skip for replay**. No source-owned parser or apply repair is
  justified.

### `2016/264` — Valtioneuvoston asetus juurikäävän torjunnasta

- Current row (`20260618T2039`): structural `1.000000`, Levenshtein
  `0.908812`; single-statute check reports Levenshtein error `9.12%`.
- `lawvm oracle-check 2016/264` reports `ORACLE_STALE=2`.
- Structural-review visible gaps are itemization/topology differences in
  `1 §` and shifted item wording in `3 §`.
- Disposition: **skip for replay** for this Levenshtein-worst pass. The
  current classifier attributes the residuals to stale oracle state; do not
  rewrite parser/item topology from the oracle without a source-owned witness
  and a failing phase-local case.

### `1995/386` — Sähkömarkkinalaki

- Current row (`20260618T2039`): structural `0.902913`, Levenshtein
  `0.914210`; single-statute check reports structural error `9.71%` and
  Levenshtein error `8.58%`.
- `lawvm oracle-check 1995/386` reports a mixed residual set:
  `EDITORIAL_CONVENTION=26`, `REPLAY_MISSING=5`, `SOURCE_INCOMPLETE=3`,
  `REPLAY_EXTRA=2`, `ORACLE_STALE=2`, `EXTRA=10`, `MISSING=1`, `UNKNOWN=1`,
  plus `DESTRUCTIVE_SHAPE_LOSS_RISK`.
- Dominant source family:
  - Large `2004/1172` recodification/restructuring wave: `kumotaan 54 §`,
    broad changes to chapters/sections, mass heading insertions, new chapters,
    and old/new chapter placement interactions.
  - Structural-review shows repeated Finlex-only amendment-date headings from
    `2004/1172`, LawVM-only old-location sections such as
    `chapter:10/section:48`, `chapter:10/section:50`, `chapter:10/section:55`,
    and paired old/new-location issues around `25 d §`, `26 §`, `27 f §`,
    `38–43 §`, etc.
- Phase conclusion:
  - This is not a narrow parser glyph or target-binding bug. It likely needs a
    source-owned recodification/migration family for broad chapter replacement,
    moved section lineage, and heading-only facet projection.
  - Any quick fix would risk deleting/moving legal state by oracle coincidence.
- Disposition: **defer broad replay work**. Return only with a named
  recodification/migration plan, source witnesses from `2004/1172` and
  synthetic migration tests proving old-location sections are neither silently
  retained nor silently deleted.

### `1922/148` — Kielilaki

- Baseline row (`20260618T2039`): structural `0.678571`, Levenshtein
  `0.922825`; `oracle-check` reported `EDITORIAL_CONVENTION=4`,
  `REPLAY_EXTRA=6`, `REPLAY_MISSING=3`, `UNKNOWN=1`.
- Root cause fixed:
  - Source amendment `1935/141` uses the historical passive formula
    `... kielilain 2, 3, 5, 6, 9, 10, 12, 13, 16, 17, 18, 20 sekä 21 § ...
    on muutettava näin kuuluviksi:`.
  - Clause parse previously emitted no ops because `muutettava` was not a
    Finnish operation verb, `kuuluviksi:` was not an END sentinel, and the
    live replay precompile gate did not treat `muutettava` as operative.
  - Added owned parser normalization
    `fi.johtolause.historical_passive_preverbal_replace.v1`: it moves only
    the witnessed pre-verbal structural enumeration before the verb-led
    parser and excludes the provenance re-mention `näistä 20 § ...`.
- Verification:
  - Parser regression emits exactly 13 `REPLACE section` ops and only one
    `20 §` target.
  - Live replay now shows 13 compiled ops from `1935/141`; section `20 §`
    changes from old railway-authority wording to the 1935 railway-area text.
  - `oracle-check 1922/148` improves to `98.6%` with residuals reduced to
    `EDITORIAL_CONVENTION=8`, `REPLAY_EXTRA=1`, `UNKNOWN=1`.
- Disposition: **fixed** parser/acquisition gate gap. Remaining residuals are
  text/editorial cleanup and a commencement-clause tail, not the original
  structural missing-amendment family.

### `1948/404` — Sotilasvammalaki

- Baseline row (`20260618T2235`): structural `0.777778`, Levenshtein
  `0.925357`; 70 amendments.
- Root cause fixed:
  - Source amendment `1955/345` says
    `lisätään ... sotilasvammalakiin (404/48) uusi, näin kuuluva 45 a §:`.
  - The Finnish insertion grammar already owned the archaic `näin kuuluva`
    lead-in but only immediately after `uusi`; the comma form compiled zero
    ops, leaving oracle section `chapter:6/section:45a` missing from replay.
  - `80ad6525` adds the narrow comma-tolerant skip for `[, ] näin kuuluva`
    after `uusi` without accepting bare `uusi, N §` forms.
- Verification:
  - `tests/test_fi_grammar_insertions.py -k nain_kuuluva` passes in a clean
    worktree.
  - `lawvm inspect-amendment 1948/404 --source 1955/345 --stage all` now
    reports `Compiled ops (1): INSERT 6 luku 45a §`.
  - `lawvm diff 1948/404 --text --threshold 0.999 --compile-summary` now
    reports `49 compared`, `0 missing from replay`, `6 extra in replay`.
- Residual disposition:
  - Extra replay sections `chapter:5/section:35` through `40` are retained
    from the source law and absent from the cached XML oracle. Prior
    `oracle-check` classified this as stale oracle/XML topology
    (`ORACLE_STALE=6`, `html-topology: missing_from_xml=35–40 §`).
  - **Skip these residual extras for replay** unless a source-owned repeal or
    migration witness is found. Do not delete transitional provisions by
    oracle absence alone.

### `1999/1179` — Opetusministeriön päätös museoviraston suoritteiden maksullisuudesta

- Baseline row (`20260618T2235`): structural `0.857143`, Levenshtein
  `0.925492`; one amendment.
- Root cause fixed:
  - Source amendment `2000/1157` explicitly targets `6 §:n 1 momentti`.
  - Source XML encodes the changed museum fee table as one targeted subsection
    lead-in (`Museo mk`) followed by textual row sibling subsections
    (`Alikartano 15`, `Hvitträsk 25`, `Olavinlinna 30`, ...).
  - Sparse slot binding previously mapped only slot `1:1` to the replace op
    and left table rows as unassigned sparse slots, so replay dropped the fee
    rows from `6 §`.
  - `a089962f` adds `ELAB.TEXT_TABLE_ROW_CONTINUATION`: a narrow payload
    normalization rule that folds row-like sibling subsections into the single
    explicitly targeted subsection when the target has a `seuraavasti:` table
    lead-in.
- Verification:
  - Synthetic positive and negative payload-normalize tests pass.
  - `lawvm inspect-amendment 1999/1179 --source 2000/1157 --stage all`
    now maps `REPLACE 6 § 1 mom` to a subsection payload containing the fee
    rows.
  - `lawvm diff 1999/1179 --text --threshold 0.999 --compile-summary`
    improves to `7 compared`, `6 perfect`, score `99.92%`; `6 §` is no
    longer listed as divergent.
- Residual disposition:
  - Remaining visible diff is `7 §` editorial residue (`Voimaantulo 1Tämä`
    vs `Voimaantulo Tämä`).
  - The row also has an oracle cutoff wrinkle: the selected oracle reflects
    `2000/1157` even though the amendment takes effect after the cached
    consolidation cutoff. Do not use this row to tune temporal selection
    without a broader oracle-horizon rule.

### `1999/488` — Laki lääketieteellisestä tutkimuksesta

- Current row (`20260619T0157`): structural `1.000000`, Levenshtein
  `0.758672`; 14 amendments.
- Re-triage commands:
  - `uv run lawvm diff 1999/488 --text --threshold 0.999 --compile-summary`
    reports `40 compared`, `39 perfect`, no replay-missing sections, no
    replay-extra sections, and one oracle stub-only section (`21 b §`).
  - `uv run lawvm oracle-check 1999/488` reports `75.8%` over `13`
    diverging sections, all classified as `EDITORIAL_CONVENTION`, with
    missing XML topology for `6 a §`, `10 d §` through `10 i §`, `14 §`,
    `25 §`, and `26 §`.
  - `uv run lawvm bench --statute 1999/488 --no-save --top 5` reproduces
    primary structural accuracy `100.00%` and secondary full-text
    Levenshtein `75.87%`.
- Root-cause class:
  - This is a **metric-surface false positive for the Levenshtein work queue**.
    Section comparison uses structured section extraction plus the FI
    comparison normalizer and finds no source-owned replay gap.
  - The secondary Levenshtein lane compares whole-statute flattened
    `master.serialize_text()` against flattened Finlex ground truth. It still
    sees editorial convention/topology surfaces that the primary structural
    and `oracle-check` lanes already classify.
- Disposition: **skip for replay/parser work**. Do not change johtolause,
  target resolution, or replay text for this row unless a source-owned section
  divergence reappears. A future benchmark cleanup may add an adjusted
  full-text lane, but that is a reporting change, not legal-state repair.

### `2005/623` — Laki alusliikennepalvelulain muuttamisesta

- Current row (`20260619T0829`): structural `0.754098`, Levenshtein
  `0.832737`; 14 amendments.
- Re-triage commands:
  - `uv run lawvm diff 2005/623 --text --threshold 1.0 --compile-summary`
    reports 61 compared sections, 42 perfect, and residual wording changes
    concentrated in the 2018 transport-authority rename wave plus later
    `2023/1311` changes.
  - `uv run lawvm inspect-amendment 2005/623 --source 2018/576 --stage all`
    shows a broad whole-section rewrite wave whose payload still contains
    `Liikenteen turvallisuusvirasto` / `Liikennevirasto`.
  - `uv run lawvm inspect-amendment 2005/623 --source 2018/947 --stage all`
    shows the corresponding authority-renamed payload with
    `Liikenne- ja viestintävirasto` / `Väylävirasto`.
  - Direct metadata inspection now classifies `2018/947` as
    `(None, 'contingent_text')`; its entry-into-force text says
    `Tämän lain voimaantulosta säädetään erikseen lailla.`
  - Source act `2018/937` explicitly brings `laki alusliikennepalvelulain
    muuttamisesta (947/2018)` into force on `2019-01-01`.
- Root-cause class:
  - **External commencement carrier frontier**. The text-writing act
    `2018/947` is correctly a contingent/deferred act; the force-activating
    act is separate (`2018/937`). Current replay still orders the `2018/947`
    operations by source fallback/sequence, then lets `2018/576` (`2019-01-01`)
    overwrite many same targets with older authority names.
  - This is not a johtolause target parser gap and should not be fixed by
    authority-name substitution or by reordering `2018/947` by statute number.
    The proper fix is a typed external commencement link from `2018/937` to
    `2018/947`, with provenance on `OperationSource.commencement_source` or
    equivalent temporal-event evidence.
- Disposition: **defer replay fix until external commencement linking is
  implemented**. Do not patch the section text or invent a local precedence
  rule for `2018/576`/`2018/947`. The metadata classifier gap for
  `erikseen lailla` is fixed separately so future diagnostics expose the
  unresolved temporal carrier.

### `2013/1201` — Laki rajat ylittävästä terveydenhuollosta

- Current row (`20260619T0832`): structural `1.000000`, Levenshtein
  `0.842430`; 6 amendments.
- Re-triage commands:
  - `uv run lawvm diff 2013/1201 --text --threshold 0.999 --compile-summary`
    reports 40 compared sections, 40 perfect, no replay-missing sections, and
    no replay-extra sections.
  - `uv run lawvm oracle-check 2013/1201` reports `84.2%` over 4 diverging
    sections, all classified as `EDITORIAL_CONVENTION`, with cached XML
    topology missing `10 §` and `16 §`.
  - `uv run lawvm bench --statute 2013/1201 --no-save --top 5` reproduces
    primary structural accuracy `100.00%` and secondary full-text
    Levenshtein `84.24%`.
- Root-cause class:
  - This is a **metric-surface false positive for the Levenshtein queue**.
    The section-level structural comparator and oracle adjudicator find no
    source-owned replay gap.
  - The low whole-statute Levenshtein score is caused by flattened text/oracle
    presentation differences that the primary structural lane already
    normalizes or classifies.
- Disposition: **skip for replay/parser work**. Do not change johtolause,
  target resolution, or replay text for this row unless a source-owned section
  divergence reappears. This row is evidence for improving/reporting the
  secondary full-text metric, not for legal-state mutation.

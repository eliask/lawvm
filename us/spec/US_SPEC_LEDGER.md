# U.S. federal spec-discovery ledger (eval re US, witness-attribution frame)

This is the U.S. federal sibling of the FI/UK/EE/NZ spec-discovery ledgers: it turns
the undifferentiated dry-run coverage number (**23 / 231 = 0.0996** witness-anchored
section agreement over the committed bench corpus) into a **ranked per-rule table** of
specific, falsifiable hypotheses about U.S. amendment law, with how often the published
USC after-edition oracle **corroborates** vs **contradicts** each one.

Produced by `lawvm.us_federal.spec_ledger_adapter` (standalone
`python -m lawvm.us_federal.spec_ledger_adapter`, or `lawvm us-spec-ledger`), which
reuses the jurisdiction-neutral core (`lawvm.tools.spec_ledger.build_ledger`) and the
catalog in `lawvm.tools.spec_ledger_us_catalog` (**46 rules cataloged**, 100 % coverage,
no uncataloged blind spots). Read-only; never authorizes replay.

- **Witness**: the published USC annual-edition after-text (the dry-run oracle). The
  oracle is a *witness, not ground truth* — a residual carries a disposition
  (`lawvm_wrong` / `oracle_suspect` / `missing_source`) so a rule is never refined to
  fit an oracle/editorial artifact.
- **Firings**: (a) compiled-op `witness_rule_id` for the amendatory family (re-derived
  by lowering each window's public laws); (b) per-section dry-run outcome rule_id; (c)
  the north-star synthetic residuals (missing_source / sunset); (d) typed refusals.
- **Corroboration / contradiction**: an `agrees` section row corroborates the lowering
  hypotheses for that section; a residual row contradicts them with a side-of-the-gap
  disposition. Refusals fire but never contradict — they are the coverage frontier.

Corpus: `us/bench/us_bench_corpus.csv` (13 windows evaluated, 0 skipped here; 2 marked
empty-delta upstream). Numbers below are a snapshot — they move as lowering improves.

## Ranked rules (contradicted desc, then divergences)

| rank | rule_id | conf | firings | corrob~ | contradicted | dispositions |
|---|---|---|---|---|---|---|
| 1 | us_dry_run_residual_materialized_text_mismatch_with_oracle | heuristic | 37 | 0 | **30** | lawvm_wrong:30 oracle_suspect:7 |
| 2 | us_dry_run_residual_match_text_not_found_in_before_section | certain | 12 | 0 | **12** | lawvm_wrong:12 |
| 3 | us_dry_run_residual_claimed_section_unchanged_in_oracle | certain | 8 | 0 | **8** | lawvm_wrong:8 |
| 4 | us_dry_run_residual_subsection_target_node_not_located_in_before_section | certain | 6 | 0 | **6** | lawvm_wrong:6 |
| 5 | us_dry_run_residual_oracle_changed_section_not_claimed | certain | 144 | 0 | 0 | missing_source:144 |
| 6 | us_sunset_temporal_note_present_but_reversion_unproven | certain | 77 | 0 | 0 | oracle_suspect:77 |
| 7 | us_sunset_temporary_provision_reverted_to_prior_permanent | heuristic | 11 | 0 | 0 | oracle_suspect:11 |
| 8 | us_amendatory_unlowered | certain | 23546 | 23546 | 0 | — |
| 9 | us_amendatory_target_unresolved | certain | 22050 | 22050 | 0 | — |
| 10 | us_dry_run_refused_target_outside_proof_title | certain | 4853 | 4853 | 0 | — |
| 11 | us_amendatory_target_non_us_code | certain | 4929 | 4929 | 0 | — |
| 12 | us_amend_strike_insert | heuristic | 2450 | 2450 | 0 | — |
| 13 | us_amend_add_at_end | heuristic | 1532 | 1532 | 0 | — |
| 14 | us_amend_to_read | heuristic | 454 | 454 | 0 | — |
| 15 | us_amend_insert_after_anchor | heuristic | 311 | 311 | 0 | — |
| 16 | us_amend_repeal | heuristic | 107 | 107 | 0 | — |
| 17 | us_amend_strike | heuristic | 92 | 92 | 0 | — |
| 18 | us_amend_redesignate | heuristic | 29 | 29 | 0 | — |
| 19 | us_dry_run_section_materialized_text_matches_oracle | certain | 23 | 23 | 0 | — |
| 20 | us_dry_run_refused_target_section_not_present_in_before_edition | certain | 4 | 4 | 0 | — |
| 21 | us_dry_run_refused_structural_op_not_representable_at_section_granularity | certain | 2 | 2 | 0 | — |

`corrob~` is a derived estimate (firings minus directly-attributed divergences), not a
counted agreement — see the honest read below for why the amendatory rows' huge `corrob~`
is **not** evidence those rules are right.

## Honest read

### Where the oracle most CONTRADICTS us (= highest-value lowering fixes)

The contradiction signal is concentrated in **four dry-run outcome rules**, all
`lawvm_wrong`-dominated:

1. **`us_dry_run_residual_materialized_text_mismatch_with_oracle` (30 contradicted)** —
   the top fix target. Our composed section text disagrees with a genuinely
   oracle-changed section. 7 of the 37 firings are `oracle_suspect` (the OLRC
   quote-stripping / courtesy-spacing editorial projection already absorbs those), so
   **30 are real materialization defects**. Concentrated in `title11:2018->2020`,
   `title11:2020->2022`, `title18:2020->2022`. This is where multi-op section
   composition and text-patch fidelity most need work.
2. **`us_dry_run_residual_match_text_not_found_in_before_section` (12)** — an op's quoted
   `match_text` is absent from the before/running section text. Either an upstream
   composition order bug (an earlier op already rewrote the anchor) or a quote-extraction
   defect in lowering. Spread across 5 windows (title11, title18 ×3, title38), so it is a
   *systematic* lowering-fidelity class, not a one-statute fluke.
3. **`us_dry_run_residual_claimed_section_unchanged_in_oracle` (8)** — we materialized a
   section the oracle never changed: a spurious claim (an op targeting the wrong section,
   or an op that should have been a no-op). Cheap, high-signal bugs.
4. **`us_dry_run_residual_subsection_target_node_not_located_in_before_section` (6)** — a
   sub-section-scoped op named a node the section split does not expose. This is the
   source-tree subsection-parse frontier feeding back as a lowering miss.

**Statutes where real bugs concentrate** (efficient mining targets):
`title18:2020->2022` (15), `title38:2023->2024` (12), `title11:2018->2020` (10),
`title18:2023->2024` (7), `title11:2020->2022` (4).

### What is well-corroborated

- **`us_dry_run_section_materialized_text_matches_oracle` (23 firings, 0 contradicted)** —
  the agreement witness. These 23 sections compose and match the oracle exactly; this is
  the 23 in 23/231 and the only *honestly corroborated* lowering evidence in the ledger.
- The **typed refusals** (`refused_target_outside_proof_title` 4853, etc.) and the
  **missing_source** north-star residual (144) are well-formed coverage-frontier
  accounting, not bugs: an off-title op is correctly refused, and an oracle-changed
  section we never claimed is honestly logged as a lowering gap rather than guessed.

### The dominant signal is a coverage wall, not a correctness wall

The largest firing counts are findings that fire **before** any oracle comparison and
therefore carry **no contradiction signal**:

- `us_amendatory_target_unresolved` (22 050) and `us_amendatory_unlowered` (23 546):
  the overwhelming majority of classified amendatory instructions across these windows
  **never reach a target / never lower to an op**. The amendment-instruction parser +
  target resolver is the binding constraint on US coverage — far upstream of
  materialization fidelity.
- `us_amendatory_target_non_us_code` (4 929) and `us_dry_run_refused_target_outside_proof_title`
  (4 853): instructions that legitimately fall outside the proof title's section frame.

**Caveat on the amendatory rows' `corrob~`:** because the dry-run surface folds each
section's lowering hypotheses into its *outcome* rule, it does not attribute a per-op
contradiction back to e.g. `us_amend_strike_insert`. So those rows show `contradicted=0`
and a `corrob~` equal to their firing count — this means "**lowered without a directly
attributed contradiction**", NOT "verified correct against the oracle". The real
correctness verdict on the amend-family lives in the four `us_dry_run_residual_*` rows
above. Treat the amendatory `corrob~` as a *firing volume*, not a quality score.

### Uncataloged blind spots

**None.** Every fired US rule_id (21 distinct, this corpus) maps to a believed_spec; the
catalog covers all 46 statically discoverable `us_*` witness rule ids, anti-drift-guarded
by `tests/test_jurisdiction_starter_us_federal_spec_ledger.py`. The 7 documented
non-rule `us_*` literals (jurisdiction name, inventory tag, provenance tag,
agreement-surface identity constants, mutation-boundary-proof outcome id, an f-string
prefix) are excluded from the rule denominator.

### Bottom line

The 23/231 bench number decomposes into two distinct frontiers:

- **A coverage frontier** (the binding one): ~45k amendatory instructions never resolve
  a target or never lower. Fixing the instruction parser / target resolver is the
  highest-leverage move on the coverage fraction.
- **A correctness frontier** (56 `lawvm_wrong` residuals): of the sections we DO claim,
  the dominant defect class is materialized-text mismatch (30) and match-text-not-found
  (12) — multi-op composition order and quote-extraction fidelity. These are the
  highest-value *lowering-fix* targets once an instruction does reach materialization.

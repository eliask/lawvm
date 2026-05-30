# UK LawVM — Living Roadmap & Idea List

The durable, committed roadmap for the UK frontend. Update this as work lands or
plans change (it supersedes any dated `.tmp/*ROADMAP*` scratch file). Companion
docs: `notes/UK_OFFICIAL_DRAFTING_SOURCE_LEDGER.md` (authority rules → destinations),
`AGENTS.md §2.1` (the correct-by-construction telos).

## Frame (don't lose this)
- Terminal product = correct-by-construction consolidation; **divergence is the
  interim product**. The gate is **source-faithfulness + invariants, not oracle
  overlap**. EID-similarity is a regression guard that *rewards over-repeal and
  spurious grounding* — near the frontier it is saturated and misleading.
- Verify any change against the broad baseline, not the 9-statute gate:
  `uv run python scripts/uk_broad_baseline.py --ids $(cat scripts/baselines/uk_grounding_corpus.txt) --out .tmp/after.json`
  then `--compare scripts/baselines/uk_broad_2026-05-30.json .tmp/after.json`.
  A score *drop* that corresponds to removing an over-applied / spuriously-grounded
  match is a correctness gain — confirm at the EID level, never trust the aggregate.
- Discipline: confirm a fix moves its **minimal repro** before broad-testing or
  committing; don't build guards for hypothetical bugs (find a real failing case).

## Instruments (built, reuse these)
- `scripts/uk_broad_baseline.py` — farchive-native scorer (`--one/--ids/--sample/--compare`),
  aligned + structural lanes, subprocess-per-statute.
- `scripts/baselines/uk_broad_2026-05-30.json` + `uk_grounding_corpus.txt` — 77-statute gate.
- `lawvm -j uk oracle-check / classify / diff / invariant-bisect / uk-misses / ops / uk-acquire`.

## §6.8 prospective effects — RESOLVED for the current corpus (sensor + primitive)
- Sensor done (`uk_prospective_effect_applied_to_current`); commencement-lookup
  primitive done + validated (`affecting_act_commencement.py`, reads the affecting
  act's per-provision `RestrictStartDate`).
- **Apply-gating is UNNECESSARY here (verified, conclusive).** Corpus scan of 325
  prospective-only structural effects across the 77-statute gate: **190 resolve as
  actually in-force** (the feed's `prospective` flag is stale → they are correctly
  applied), **0 are genuinely future** (`RestrictStartDate > now`), 135 unresolved
  (affecting act not cached, or schedule-paragraph granularity below the primitive).
  So there is no uncommenced over-application to gate on this corpus, and gating
  would only wrongly drop commenced effects (which is exactly why the blanket gate
  was mixed-sign). The sensor is the correct deterministic treatment.
- **Where the primitive WILL matter:** PIT compiles and recent statutes (e.g.
  `ukpga/2025/18`'s not-yet-commenced provisions) — there, genuinely-future affecting
  provisions exist, and the primitive supplies the non-guessing apply decision.
  Wire it into apply-gating only when a corpus with `RestrictStartDate > as_of`
  effects is in scope; verify on the broad baseline then.
- **New lead (separate bug):** `ukpga/1996/5` +6.86 when its one prospective effect
  is dropped is NOT commencement (the affecting provision is in force) — it is a
  **`repealed in part` over-application** (the partial repeal removes more than its
  part). Investigate the partial-repeal scope, not commencement. Added to backlog.

## In progress
- (none — §6.8 resolved to sensor + primitive above; pick the next backlog item)
  - **Resolver is NOT feed-derivable (verified, conclusive).** Compared the
    prospective effects on `ukpga/1996/5` (gating helped → oracle does NOT reflect
    them) vs `ukpga/1968/20` (gating hurt → oracle DOES reflect them): the feed
    attributes are *identical* (`applied=true`, `prospective=true`, empty date, same
    structural verbs), and there is **no commencement record** in the feed for the
    reflected case. `ukpga/1968/20`'s amendments by `ukpga/1996/46` (Police Act 1996)
    are plainly in force yet still flagged prospective with no commencement effect —
    the feed's `prospective` flag is **stale/incomplete for commencement**. So a
    feed-only resolver would be guessing (forbidden, `§2.1`). The sensor is the
    correct *deterministic* endpoint until real commencement data exists.
  - **Real resolver — deterministic, data CONFIRMED, design VALIDATED:** the
    affecting act's XML carries per-provision `RestrictStartDate` (the in-force start
    date): `ukpga/1996/46` has 258 occurrences, `ukpga/1999/8` 280, both cached. Per
    prospective effect: parse `affecting_provisions`, find the affecting provision's
    element in the affecting act, read its `RestrictStartDate`; the effect is
    commenced iff that date ≤ compile point-in-time. Apply iff commenced, else hold
    in the prospective lane (owned claim). Validated signal: 1968/20's prospective
    repeals come via `ukpga/1996/46` s.17 / Sch.7 Pt.3 — section 17's
    `RestrictStartDate` is `2009-10-31` (in force → should apply → oracle reflects
    them ✓); 1996/5's lone prospective effect is via `ukpga/1999/8` Sch.5 (→ hold ✓).
    **Remaining work (focused feature):** robust affecting-provision addressing —
    `affecting_provisions` is often a **Schedule** (`Sch. 7 Pt. 3`, `Sch. 5`), not a
    section, so the element lookup must resolve sections AND schedule parts/paras and
    read their `RestrictStartDate`; then wire per-op apply-gating and verify on the
    broad baseline (predict net improvement: the mixed-sign cases resolve correctly).
    NOT a one-liner; build the provision-address→start-date primitive + tests first
    (replay-neutral), then the apply-gating step (replay-changing, broad-verify).

## Ranked backlog (highest correctness value first)
1. **§6.8 resolver** (above) — biggest remaining correctness lever.
2. **DONE — `repealed in part` overwrite-with-repeals-table** (`2ce8c213`, +6.86 on
   `ukpga/1996/5`→100). A repeal-family effect whose source is a repeal Schedule was
   lowering to a whole-node replace that overwrote the target with the repeals table;
   now withheld (target preserved), while the quoted-words `text_repeal` path is kept.
   **Residual (hypothesis REFUTED 2026-05-30, verify-before-building):** the supposed
   `ukpga/1968/20` (+0.04) / `ukpga/1968/70` (+0.29) "flattened repeals-table" residual
   does **not** exist. Inspected every whole-node REPLACE op (payload, no text_patch) on
   both statutes: 1968/70 has exactly 1 (a legitimate `section:10/subsection:6` definitions
   substitution from `uksi/2009/2054`), and all 26 on 1968/20 are genuine `"For section X
   substitute—"` amendments — none is a repeal table (`repeal_ish=False` for all 26).
   There is nothing to withhold here; doing so would drop real amendments (Prime Directive
   violation, over-erasure). Do **not** extend the detector for these statutes.
3. **#53 / Theft-Act 24A + 1998/17 17C/D/E** — commencement + spurious-grounding
   tangle; resolve the in-force/commencement question (feeds §6.8). Re-land #52
   letter-suffix matcher only after (`_uk_section_label_in_simple_list`,
   `\d+[A-Za-z]*` + substring guard; patch+test drafted, reverted).
3. **§6.4 `UK_RULE_INSERTED_PROVISION_EID`** (direction b) — DIAGNOSED 2026-05-30,
   re-narrowed: the inserted-provision *number* is already derived right; the only gap
   is **eId letter-case** (synthesis lowercases `section-20a`; oracle uses `section-20A`).
   Verified failing case `1978/30` section 20A (fuzzy:0.978). Fix = a `_uk_eid_canonical_
   number` applied at eId-attribute OUTPUT sites only, NOT the lowercase eid_map-key /
   flat-candidate matching sites (the hazard). LOW/zero score impact (fuzzy already fixes
   output); pure determinism. Full spec + site list in
   `UK_OFFICIAL_DRAFTING_SOURCE_LEDGER.md` §6.3/§6.4. Ready to build with broad-baseline
   guard; deferred from the autonomous pass as cross-cutting + needs before/after validation.
4. **Schedule-1 crossheading representation** (ukpga/1978/30) — INVESTIGATED, verdict
   = **oracle-editorial eId convention, not a bug; do NOT chase.** The crossheadings
   themselves match; only the paragraphs *under* them diverge by eId SCHEME: oracle
   uses a positional `<crossheading>_paragraph-wrapperNnM`, replay uses label-based
   `<crossheading>-paragraph-<letter>` (and some crossheading-child paragraphs get no
   structural eId, so they fall to fuzzy/local-fallback). The legal content (the
   Interpretation Act definitions) is present in the enacted base — this is an
   addressing-convention mismatch, and replay's label-based scheme is arguably more
   stable than the oracle's opaque positional `wrapperNnM`. Matching it would be
   benchmaxxing the oracle's convention (forbidden, §2.1). The ~78% on 1978/30 is
   essentially source-faithful; the residual is editorial eId scheme.
5. **§6.1 repeal guards** — `REPEAL_OF_REPEAL_NO_REVIVE` (Interpretation Act 1978
   s.15), `REPEAL_NO_DOUBLE_ENTRY`. Speculative — find a real corpus case first.
6. **Effect-verb lowering long tail** — the remaining ~24/109 `no_supported_action`
   on 1978/30 (e.g. `replaced (by …)`, amendments-to-amending-acts per OPC §6.8.4).
7. **Layer-2 mutation-boundary detector** (AGENTS.md §9, task #57) — ALREADY BUILT,
   do not rebuild: `core/mutation_boundary.py` + `mutation_events.py` +
   `mutation_accounting.py`, wired via `UKReplayInvariantDiagnosticsMixin` /
   `UKReplayExecutor(mutation_events_out=)`, tested (`test_mutation_boundary.py`,
   `test_mutation_events.py`), exposed via `invariant-bisect`. Remaining (optional):
   verify it flags the grounding-collateral class and surface
   `unexplained_changed_paths` in oracle-check buckets.
8. **Source mining follow-up** — mine SIP (SI replay/vires), CLS (mechanism families,
   more MeVM-relevant), devolved manuals; extend the source ledger.

## Done this session (2026-05-30)
- Broad baseline scorer + curated gate; Track G source ledger.
- §6.9 non-textual modification classification; §6.3.8 `words added` lowering;
  §6.8 prospective-effect sensor. (All replay-neutral source-fidelity wins.)
- Verified delegated `oracle-check/classify/diff -j uk` commit.

## Over-production triage (replay produces more EIDs than oracle)
Scanned the baseline for `replay > oracle` (the forbidden over-application direction).
Most are NOT cleanly fixable — do not chase without a new angle:
- **Feed-incompleteness / old acts repealed elsewhere** — `ukpga/1966/42` (replay 509
  vs oracle 43, 8.4%), `1951/30`, `1972/5`: tiny oracle stubs, only ~22 effects, so the
  bulk repeals are simply not in the modern effect feed. Missing source, not a bug.
- **EU lane** — `eur/2019/2018` (aligned 15.2%, unaligned 100%, **0 ops**): NOT a
  `ground_ids()` corruption (hypothesis **refuted** 2026-05-30, verify-before-building).
  Root cause is an oracle-side extractor inconsistency: the source XML carries **zero**
  `eId` attributes (all EU-lane eIds are synthesized by LawVM), and the two synthesizers
  disagree — `extract_eid_map_bytes` mints 72 ids *including* annex paragraphs
  (`annex-I-paragraph-N`), while `parse_uk_statute_ir_bytes` synthesizes only 59 and
  **drops** annex-paragraph eIds. Grounding's `local_fallback` then mints annex eIds on
  the *replay* side (327 nodes; only 16/327 coincide with an oracle eid_map value), so
  grounded-replay carries ~329 eIds while the ungrounded oracle-IR baseline carries ~50
  → the scorer compares grounded-replay vs ungrounded-oracle and reports 15%. Grounding
  is doing label-only work (node count unchanged); the gap is the annex-eId asymmetry,
  not replay over-application. The correct-by-construction fix is to make annex paragraphs
  eId-bearing on **both** sides (parser + eid_map agree) — a broad UK/EU parser change
  with every-statute blast radius, so it needs a full broad-baseline before/after and is
  best validated on the broadened corpus after the `uk-corpus` fetch lands. Do **not**
  "fix grounding" — it is not the defect.
- **Representation** — `ukpga/1980/65` (+842), `2008/17` (+196): crossheading/wrapper
  structural differences vs oracle editorial form (saturated frontier).
- The one **clean** over-application bug found here was the repeals-table overwrite
  (fixed, item 2 above). The rest need source acquisition or are oracle-editorial.

## Verified NOT wins (don't repeat)
- Score-ranked grounding rewrite (net-negative, built on a misdiagnosis).
- Blanket prospective gate (mixed-sign).
- Territorial-extent qualifier normalization (0 population in the dropped lane).
- Inert oracle-check non-textual bucket tweak.
- **EU-lane `ground_ids()` "corruption"** (`eur/2019/2018`): refuted — grounding is
  label-only; the 15% is an oracle-side annex-eId asymmetry between
  `extract_eid_map_bytes` and `parse_uk_statute_ir_bytes` (see Over-production triage).
- **`1968/20`/`1968/70` flattened-repeals-table residual**: refuted — every whole-node
  REPLACE on both is a legitimate substitution, no repeal table to withhold.

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
   **Residual:** `ukpga/1968/20` (+0.04) and `ukpga/1968/70` (+0.29) have the same bug
   class but a *flattened* repeals-table source that `_looks_like_repeal_schedule_table_source`
   doesn't match — small; extend the repeals-table detector if picking this up.
3. **#53 / Theft-Act 24A + 1998/17 17C/D/E** — commencement + spurious-grounding
   tangle; resolve the in-force/commencement question (feeds §6.8). Re-land #52
   letter-suffix matcher only after (`_uk_section_label_in_simple_list`,
   `\d+[A-Za-z]*` + substring guard; patch+test drafted, reverted).
3. **§6.4 `UK_RULE_INSERTED_PROVISION_EID`** (direction b) — structural eIds for
   inserted subtrees so grounding is exact not fuzzy. LOW score impact (fuzzy is
   rare ~2 nodes/statute); do for correctness-by-construction, not score.
4. **Schedule-1 crossheading representation** (ukpga/1978/30's 50 deterministic-gaps)
   — replay vs oracle editorial crossheading/`paragraph-wrapper` structure. Saturated
   frontier; clarify whether it is our bug or oracle-editorial before touching.
5. **§6.1 repeal guards** — `REPEAL_OF_REPEAL_NO_REVIVE` (Interpretation Act 1978
   s.15), `REPEAL_NO_DOUBLE_ENTRY`. Speculative — find a real corpus case first.
6. **Effect-verb lowering long tail** — the remaining ~24/109 `no_supported_action`
   on 1978/30 (e.g. `replaced (by …)`, amendments-to-amending-acts per OPC §6.8.4).
7. **Layer-2 mutation-boundary detector** (AGENTS.md §9, task #57) — post-replay
   over-repeal/collateral detector; core-level, jurisdiction-agnostic.
8. **Source mining follow-up** — mine SIP (SI replay/vires), CLS (mechanism families,
   more MeVM-relevant), devolved manuals; extend the source ledger.

## Done this session (2026-05-30)
- Broad baseline scorer + curated gate; Track G source ledger.
- §6.9 non-textual modification classification; §6.3.8 `words added` lowering;
  §6.8 prospective-effect sensor. (All replay-neutral source-fidelity wins.)
- Verified delegated `oracle-check/classify/diff -j uk` commit.

## Verified NOT wins (don't repeat)
- Score-ranked grounding rewrite (net-negative, built on a misdiagnosis).
- Blanket prospective gate (mixed-sign).
- Territorial-extent qualifier normalization (0 population in the dropped lane).
- Inert oracle-check non-textual bucket tweak.

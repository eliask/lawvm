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

## In progress
- **§6.8 PIT-aware prospective resolver** — decide application of prospective-only
  structural effects per the oracle version / `authority_mode`, instead of silently
  applying. Sensor phase done (`uk_prospective_effect_applied_to_current`). Blanket
  gate verified WRONG (mixed-sign +6.86…−3.99): oracle reflection is PIT/editorial
  dependent. NEXT STEP: find the *source* signal distinguishing prospective effects
  the oracle reflects from those it doesn't (compare 1996/5 vs 1968/20 feed attrs);
  that signal drives the resolver. ~8.6% of structural effects are in this lane.

## Ranked backlog (highest correctness value first)
1. **§6.8 resolver** (above) — biggest remaining correctness lever.
2. **#53 / Theft-Act 24A + 1998/17 17C/D/E** — commencement + spurious-grounding
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

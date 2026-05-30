# UK Official Drafting / Practice Source Ledger (Track G)

LawVM's UK rules need not be mined only by reverse-engineering effect feeds and
XML. The UK publishes an official drafting/practice layer that is a high-value
authority for normal drafting intention: the source-only side of the
correct-by-construction compiler (`AGENTS.md §2.1`).

This ledger is **not** the executable UK replay spec. It is the source-authority
map: what official material says, what LawVM destination it informs, and which
questions remain ambiguous enough to need code/corpus verification.

`UK_REPLAY_LIVING_SPEC.md` is different: it is the reverse-engineered operational
ledger of current replay behavior, corpus witnesses, edge-case families, and
tooling contracts. When this official ledger and the living replay spec disagree,
do not choose by document prestige. Verify the source facts, current code, and
corpus witnesses, then either:

- keep the functional implementation as the current executable behavior;
- strengthen it with an official-source citation and stable rule name;
- or migrate it only with a failing case, observation/finding, strict behavior,
  and regression tests.

Authority tiers (use to rank a mined rule when it conflicts with observed
practice):

```
Tier 1: official drafting guidance (OPC / TNA / Cabinet Office / Parliament)
Tier 2: official explanatory or procedural manuals
Tier 3: observed effect-feed / XML practice
Tier 4: corpus-derived heuristics
```

A Tier-1 rule is authority for *ordinary drafting intention*, not a statute-level
oracle and not automatic proof that a replay change is correct. OPC's own preface
says the guidance is not comprehensive and drafters may depart from it. Official
guidance therefore strengthens **source-faithfulness invariants and diagnostics**;
it does not override a source witness, and it does not license benchmaxxing.

Use this decision order:

1. **Executable facts first**: source XML/feed/body text, current code behavior,
   and corpus witnesses define what LawVM actually does today.
2. **Official guidance names intent**: use OPC/TNA/Cabinet/Parliament sources to
   classify ambiguous drafting forms and choose rule boundaries.
3. **Reverse-engineered rules remain valid when witnessed**: a corpus-derived
   family in `UK_REPLAY_LIVING_SPEC.md` remains canonical until official guidance
   plus source witnesses justify changing it.
4. **Ambiguity stays explicit**: where official guidance says practice varies, or
   where source surfaces conflict, emit a manual-frontier / source-pathology /
   oracle-suspect lane rather than silently mutating text.

---

## Sources

| source_id | authority | scope | primary LawVM use | acquired |
|---|---|---|---|---|
| `OPC_DRAFTING_GUIDANCE_2024` | OPC drafting guidance (Tier 1) | UK Government Bills / OPC practice | amendment grammar, repeal/substitution/insertion semantics, non-textual modification, uncommenced material | `.tmp/uk_drafting_sources/opc/opc_drafting_guidance_2024.{html,pdf}` + extracted `.txt` |
| `OPC_AMENDING_BILLS_STYLE_MANUAL_2024` | OPC / Parliamentary amendment style (Tier 1/2) | Bill amendment forms, printing, provisional labels | bill-amendment ingestion, editorial projection boundaries | `.tmp/uk_drafting_sources/opc/amending_bills_style_manual_2024.pdf` + extracted `.txt` |
| `OPC_COMMON_LEGISLATIVE_SOLUTIONS_2025` | OPC + drafting offices (Tier 1) | policy→mechanism pattern catalogue | operation families / target-surface semantics; mostly MeVM-relevant | `.tmp/uk_drafting_sources/opc/common_legislative_solutions_2025.{html,pdf}` + extracted `.txt` |
| `GUIDE_TO_MAKING_LEGISLATION_2025` | PBL Committee secretariat (Tier 2) | bill process; delegated powers; Henry VIII; commencement powers | delegated-power, commencement, proposed/failed bill diagnostics | `.tmp/uk_drafting_sources/process/guide_to_making_legislation_2025.{html,pdf}` + extracted `.txt` |
| `STATUTORY_INSTRUMENT_PRACTICE_5TH_ED` | The National Archives (Tier 1/2) | SI preparation, vires, laying, scrutiny | SI replay & vires constraints; SI source interpretation | `.tmp/uk_drafting_sources/si/statutory_instrument_practice_5th_edition.pdf` + extracted `.txt` |
| `OPC_CARRYING_OVER_BILLS_2024` | OPC (Tier 1/2) | bill carry-over across sessions | draft/proposed/failed branch identity, bill-version lineage | `.tmp/uk_drafting_sources/opc/carrying_over_bills_2024.pdf` + extracted `.txt` |
| `OPC_CROWN_APPLICATION_2021` | OPC (Tier 1/2) | Crown application defaults and clauses | applicability metadata, jurisdiction defaults | `.tmp/uk_drafting_sources/opc/crown_application_2021.pdf` + extracted `.txt` |
| `OPC_FINANCIAL_RESOLUTIONS_2023` | OPC / Commons finance procedure (Tier 1/2) | financial-resolution cover for bill text | provisional bill-text flags, proposed-law conditionality | `.tmp/uk_drafting_sources/opc/financial_resolutions_2023.pdf` + extracted `.txt` |
| `OPC_MONEY_BILLS_2024` / `OPC_PARLIAMENT_ACT_1911_S2_2019` | OPC / Parliament Act procedure (Tier 1/2) | alternative enactment paths without Lords consent | authority/enactment-path metadata | `.tmp/uk_drafting_sources/opc/*.pdf` + extracted `.txt` |
| `ERSKINE_MAY` | UK Parliament (Tier 2) | parliamentary procedure/convention | procedural validity, consent, carry-over | no |
| devolved manuals (SSI / Welsh SI / NI rules; SP/Senedd/NIA bill guidance) | devolved bodies (Tier 1) | ASP / Welsh / NI drafting | ASP/Welsh/NI frontends (follow-up) | no |

Extraction note: `.tmp/uk_drafting_sources_text/` contains temporary `pdftotext`
and HTML text extractions. Several short SI/toolbox HTML files are fetch stubs
("Page not found"); the full SIP PDF extraction is the useful SI source. The
Scotland `Drafting Matters` extraction currently exposes mostly table-of-contents
material, so it is acquisition evidence, not enough for operative devolved rules.

Acquisition follow-up: fetch usable devolved manuals and Erskine May before
claiming the UK source stack is complete.

---

## Official-source deltas not yet fully folded into code

These are not replay changes by themselves. They are source-backed semantics to
use when a concrete failing case or architecture task needs the rule boundary.

### OPC / amendment semantics beyond the existing Part 6 queue

- **Textual amendments are always speaking** (`OPC_DRAFTING_GUIDANCE_2024`
  §6.1.8-6.1.10): repealing amendment machinery does not normally undo the
  amended text. Destination: replay/lineage evidence; do not infer reversion
  merely because an amending provision is repealed.
- **Act amendment ranges include endpoints** (`OPC_DRAFTING_GUIDANCE_2024`
  §6.2.4-6.2.5): statutory "from/to" text descriptions include the named words
  unless contrary intention appears. House-amendment line ranges follow a
  different practice. Destination: source-regime-specific text-span lowering.
- **Adding children to an undivided provision is structural migration**
  (`OPC_DRAFTING_GUIDANCE_2024` §6.3.10): first number the existing text as
  `(1)`, then insert the new `(2)`-style child. Destination: lowering/replay
  should model host-text-to-child migration rather than overwrite the parent.
- **Grouped amendment scope is closed** (`OPC_DRAFTING_GUIDANCE_2024`
  §6.7.1-6.7.2): a source statement that Act X is amended "as follows" should not
  contain amendments to Act Y. Destination: parser/source-scope diagnostics.
- **Expiry is not repeal** (`OPC_DRAFTING_GUIDANCE_2024` §10.7): expiry and
  sunset are temporal events; repeal of a repeal does not revive, but expiry of a
  repealing enactment may have different revival consequences. Destination:
  temporal/replay event typing, not a `REPEAL` alias.

### Statutory Instrument Practice

- **SI commencement default differs from Acts** (`STATUTORY_INSTRUMENT_PRACTICE_5TH_ED`
  §§1.3.5, 3.12): if no later date is given, an SI generally comes into force at
  the moment of making; a date-only commencement is at the beginning of that day
  unless contrary intention appears. Destination: SI timeline/PIT defaults.
- **Preambles are vires evidence** (`STATUTORY_INSTRUMENT_PRACTICE_5TH_ED` §3.11):
  enabling-power recitals identify the powers and validity conditions relied on.
  Destination: UK SI source metadata and future vires/condition observations.
- **SI extent and application are separate** (`STATUTORY_INSTRUMENT_PRACTICE_5TH_ED`
  §3.13): England-only or Wales-only limits are usually application, not extent;
  pure amending instruments often need no separate application provision because
  the amendment follows the amended instrument. Destination: no silent
  extent/application widening.
- **Revocation/lapse is savings-aware** (`STATUTORY_INSTRUMENT_PRACTICE_5TH_ED` §3.14):
  powers to make SIs generally imply amendment/revocation powers, parent repeal
  may make subordinate instruments lapse unless saved, and spent SIs may remain
  unrevoked. Destination: revocation/lapse classification and temporal evidence.
- **Correction slips are not amendment authority** (`STATUTORY_INSTRUMENT_PRACTICE_5TH_ED`
  §4.7): substantive errors require amending legislation; correction slips and
  reprints are for non-substantive corrections. Destination: source-lane and
  adjudication boundary.
- **SI structure vocabulary is type-specific** (`STATUTORY_INSTRUMENT_PRACTICE_5TH_ED`
  Table A): Orders use articles, Regulations use regulations, Rules use rules;
  schedules use paragraph/sub-paragraph structures. Destination: address grammar
  and source XML structural normalization.

  **Diagnostic surface added 2026-05-31:** `scripts/uk_si_semantics_scan.py`
  inventories SI source semantics without replay mutation. Current all-cached-SI
  command:
  `uv run python scripts/uk_si_semantics_scan.py --all --pretty --limit 0`.
  Result: 4,869 SI-like current XML documents scanned, 35,736 diagnostic rows:
  4,869 structure-vocabulary rows, 4,863 vires-recital rows, 4,578
  commencement-metadata rows, 7,677 body-commencement clause rows, 3,215 extent
  rows, 7,779 application rows, 2,750 revocation/lapse rows, and 5
  correction-slip marker rows. These are evidence rows only; replay-changing SI
  rules still require source-level adjudication of a concrete family.
  **Commencement fallback adjudication added 2026-05-31:** applied UK SI effects
  with no effect-level in-force date still recover a replay date only from exactly
  one official `ComingIntoForce/DateTime/@Date`. Missing source XML, parse errors,
  textual-only commencement metadata, and multi-date commencement metadata now emit
  nonblocking `uk_effect_undated_applied_si_commencement_unresolved` observations
  instead of disappearing as an invisible non-recovery. On the 77-statute broad
  gate this exposes 312 unresolved non-recoveries (295 textual/missing-date, 17
  multi-date) alongside 321 existing single-date source-backed overrides.

### Process, proposed-law, and authority paths

- **Draft bills are proposed-law artifacts** (`GUIDE_TO_MAKING_LEGISLATION_2025`
  glossary / Chapter 3): publication in draft precedes formal introduction.
  Destination: draft/proposed branches, not enacted-law replay.
- **Commencement shapes are varied** (`GUIDE_TO_MAKING_LEGISLATION_2025`
  Chapters 3, 9, 38; `OPC_DRAFTING_GUIDANCE_2024` §10.6): Royal Assent defaults,
  appointed-day instruments, fixed dates, periods after Royal Assent, purpose-
  specific commencement, transitional/saving/transitory powers, early
  commencement, and retrospection must remain distinct temporal facts.
- **Carry-over preserves bill identity conditionally** (`OPC_CARRYING_OVER_BILLS_2024`):
  carried-over bills continue across sessions on specified terms, can lapse, and
  do not survive dissolution in ordinary public-bill cases. Destination:
  proposed/failed branch state and bill-version lineage.
- **Alternative enactment routes are authority metadata** (`OPC_MONEY_BILLS_2024`,
  `OPC_PARLIAMENT_ACT_1911_S2_2019`): Speaker certification and Parliament Act
  conditions can permit Royal Assent without ordinary Lords consent. Destination:
  enactment-path evidence, not text replay.
- **Financial-resolution italics are provisional bill text**
  (`OPC_FINANCIAL_RESOLUTIONS_2023`): provisions needing Commons financial cover
  can be provisional and removed if cover is not obtained. Destination:
  conditional proposed-law text, not enacted consolidation.
- **Crown application is applicability metadata** (`OPC_CROWN_APPLICATION_2021`):
  UK Acts normally do not bind the Crown absent express words or necessary
  implication, while Scottish and Welsh defaults differ after their statutory
  changes. Destination: applicability dimensions, not replay mutation.

  **UK graph prototype added 2026-05-31:** `lawvm uk-branch-demo` emits a
  UK-shaped proposed-law branch payload using the shared branch graph contracts:
  `LegalBranch`, `BranchGraphEdge`, `BranchLifecycleEvent`, and impact projection
  rows. The adapter is structured-payload only; it does not parse bill sources and
  does not route proposed operations into default enacted replay.

---

## Mined rules → LawVM destinations (OPC Drafting Guidance Part 6)

Status legend: **HAVE** (implemented, verify), **GAP** (not handled / partial),
**SPEC** (diagnostic/spec-yield, no replay).

Implementation status is a snapshot, not an authority claim. Before acting on a
`GAP` or `HAVE`, verify current code and current corpus behavior; this file is
allowed to lag the executable implementation.

### §6.1 Repeals
- **6.1.2 operative form** `In the [Act] omit section 10 / omit Part 2`; whole-Act
  `The [Act] is repealed`. → UK repeal lowering. **HAVE (verify).**
- **6.1.3/6.1.5 repeal Schedule, no double-entry** — a repeal appears in the body
  *or* a repeal Schedule, never both (`Commissioner of Police v Simeon`). →
  `UK_RULE_REPEAL_NO_DOUBLE_ENTRY`: when the same target is repealed by both a body
  omit and a Schedule extent row, dedup to one op and emit an observation, do not
  apply twice. **HAVE.** Diagnostic command:
  `uv run python scripts/uk_repeal_semantics_scan.py --ids-file scripts/baselines/uk_grounding_corpus.txt`.
  Current 77-statute gate result: 49 duplicate repeal-target candidates, including
  8 body-plus-schedule double-entry candidates. The replay filter is intentionally
  narrower than that inventory: it drops only exact duplicate structural `REPEAL`
  operations inside a body+Schedule suffix-pair group and records
  `uk_effect_repeal_no_double_entry_duplicate_rejected`. Current regression witness:
  `ukpga/1990/8` affected `s. 203-205` by `ukpga/2008/29`, where `Sch. 13` and
  `s. 192(6) Sch. 13` previously emitted duplicate repeals for sections 203, 204,
  and 205.
- **6.1.6/6.1.7 repeal of amendments only when completely superseded** → diagnostic
  for body-repeals of amending provisions. **SPEC.**
- **6.1.13 repeal of a repeal does not revive** (Interpretation Act 1978 s.15, subj.
  s.16 savings) → `UK_RULE_REPEAL_OF_REPEAL_NO_REVIVE`: a repeal op whose target is
  itself a repealing provision must not resurrect the originally-repealed text.
  **SPEC / WITNESS SEARCH EXISTS.** The same diagnostic scan searches source text
  for no-revive / repeal-of-repeal phrases. Current 77-statute gate has no phrase
  witnesses; do not add a guard without a concrete target witness.
- **6.1.14 repealing a paragraph with a trailing conjunction** — make the `and`/`or`
  explicit. → connects to existing `tail_connector` modelling. **HAVE (verify).**

### §6.2 / §6.5 Substitutions & occurrence scope
- **6.2.1 `for x substitute y`**; **6.2.6/6.2.7 substitute vs repeal+insert** — reuse
  the number only when the new text is a *direct* replacement; otherwise repeal +
  insert *without* reusing the number (form follows function). → structural
  `replace` vs `repeal+insert` + identity/lineage. **HAVE (verify) / partial.**
- **6.5.5–6.5.8 occurrence scope** — `in the first/second/third place it occurs`,
  `in both places`, `in each place`. → `OccurrenceScope` parser. **HAVE (verify).**
- **6.5.2/6.5.3 opening/closing words** — "the words before paragraph (a)" / "after
  paragraph (c)". → text-selector grammar for intro/outro words. **HAVE/partial.**
- **6.5.10 `(including the heading)`** — an amendment may reach the heading too. →
  heading-facet selector. **GAP (verify).**

### §6.3 / §6.4 Insertions & the inserted-provision numbering algorithm
This is the **authority for direction (b)** — assign the *correct structural eId* to
an inserted provision at insert time, so oracle grounding is exact (structural) and
never has to *guess* a deep node's identity by fuzzy text.
- **6.3.1 `after x insert y`; 6.3.5 at the beginning; 6.3.6 at the end; 6.3.9 at the
  appropriate place** (alphabetical lists). → insertion anchor resolution +
  `appropriate_place` (a known manual-frontier address). **HAVE/partial.**
- **6.4 numbering of inserted provisions** (deterministic):
  - before the first in a series: lettered `A1, B1, …`; before `A1` → `ZA1`; lettered
    paras before `(a)` → `(za), (zb)`; before `(za)` → `(zza)`.
  - between `1` and `2` → `1A, 1B`; between `1A` and `1B` → `1AA`; between `1` and
    `1A` → `1ZA` (not `1AA`); between `1A` and `1AA` → `1AZA`.
  - **6.4.4** do not generate a lower level than necessary.
  - **6.4.6** after `Z` → `Z1, Z2` (e.g. after `360Z` → `360Z1`).
  - **6.4.7** do **not** re-use the number of a previously-repealed provision.
  → `UK_RULE_INSERTED_PROVISION_EID`: derive the inserted node's structural eId from
  this algorithm so `payload_identity` / synthesis assigns the address the oracle
  will use, instead of generic `paragraph-a/b/c` that fall to fuzzy grounding.
  **DONE for the diagnosed 2026-05-30 direction-b gap** (`7c6accba`): digit-led
  inserted provision eId segments preserve canonical uppercase letter suffixes
  (`section-20A`, `section-23ZA`, `section-24-3A`) while lowercase matching keys
  remain lowercase. Tests: `tests/test_uk_inserted_provision_eid_case.py`.
  Residual grounding-collateral work is not this rule; it is a measurement and
  parser/eId-map consistency problem.

### §6.8 Uncommenced material  ← the `ukpga/1998/17` class
- **6.8.7** amendments should not be in force before the provision they amend.
- **6.8.10–6.8.13** repealing an uncommenced amendment: operate on the *amended* Act
  for a *partial* repeal, but on the *amending* Act when repealing it *in its
  entirety before it ever commences* (so you don't have to commence-then-repeal).
- **6.8.14–6.8.16** amending a provision **subject to an uncommenced repeal** — the
  amendment's post-repeal effect must be considered; an express repeal of the
  amendment is often added to put it beyond doubt.
- **6.8.17/§6.8.20** transitory `has effect as if …` provisions bridge until a repeal
  commences.
→ `UK_RULE_UNCOMMENCED_EFFECT_OWNED_LANE`: prospective-only structural effects are a
  real and large population — **316 / 3659 (8.6%)** structural-for-replay effects
  across 16 / 40 sampled statutes — and `is_applicable_for_replay` currently ignores
  the `prospective` flag entirely (it gates only on `applied`/`metadata_only`), so
  they are applied to the current consolidation regardless.
  **A blanket "do not apply prospective" gate is WRONG, verified empirically:** it
  ranges from `ukpga/1996/5` +6.86 (→100%) and `ukpga/1990/9` +3.74 to `ukpga/1968/20`
  −3.99 and `ukpga/1998/17` −3.23. The sign flips because whether the *current*
  oracle XML reflects a prospective change is **point-in-time / editorial dependent**
  — not uniform. So this is a **manual-compilation-frontier** class (`§2.1`:
  prospective/contingent commencement, PIT selection), not a deterministic gap.
  Correct shape: model prospective-only structural effects as a **first-class owned
  conditional lane** — surface them as a named observation, do not silently apply,
  and let `authority_mode` / PIT selection decide application per the oracle version
  being compared (an owned claim, not a guessed blanket rule). **GAP — re-scoped from
  "gate it off" to "own the PIT-conditional lane".**

### §6.9 Non-textual modifications
- **6.9.1** a non-textual modification does **not** change the printed text (contrast
  a textual amendment which does). **6.9.4–6.9.6** the tell is the subjunctive: `…
  applies … as if … there were substituted …` / `as if it were modified as follows`.
→ `UK_RULE_NON_TEXTUAL_MODIFICATION_NOT_TEXT_REPLAY`: an effect whose source is a
  modification ("as if", "applies … with modifications", "has effect as if") must
  **not** be replayed as a textual/structural mutation; classify it as a non-textual
  modification lane. Replaying it as text is over-application. **DONE for the
  current structural replay lens** (`3bd8f524`, `550c36f7`): source adjudication
  classifies this as `uk_non_textual_modification_out_of_scope` /
  manual-frontier out-of-scope evidence rather than direct text/tree mutation.

---

## Historical implementation thread (kept for context)

The session's stuck case (`ukpga/1998/17`) and the grounding `(a)/(b)` question
motivated the first source-ledger work. This section is historical planning
context, not the current roadmap. Verify current code, corpus behavior, and any
external roadmap before treating an item below as live.

1. **Uncommenced / over-application.** `ukpga/1998/17`, the original Theft-Act
   case, and related regressions showed that prospective/current comparison is a
   PIT/editorial issue, not a blanket gate. Current state: sensor done; resolver
   remains a temporal-model question.

2. **Grounding is a crutch, not a thing to tune.** `ground_ids()` can rename replay
   eIds to match an oracle; fuzzy/local fallback can fabricate misleading apparent
   fidelity. The inserted-provision eId-casing gap is done. The current grounding
   lever is to measure and then reduce collateral minted eIds, not to chase oracle
   style for its own sake.

3. **Non-textual modification (§6.9)** is implemented for the structural replay
   lens. **No-double-entry / no-revive (§6.1)** now have a diagnostic witness
   search surface. No-revive still has no current 77-statute witness; no-double-entry
   has candidate effect-feed rows that need source-level adjudication before any
   replay-changing rule.

Verification for any of these uses the broad baseline, not the 9-statute gate:
`scripts/uk_broad_baseline.py --ids $(cat scripts/baselines/uk_grounding_corpus.txt)
--out .tmp/after.json` then `--compare scripts/baselines/uk_broad_2026-05-30.json
.tmp/after.json`. A score *drop* that corresponds to removing an over-applied or
spuriously-grounded match is a **correctness gain the guard mis-penalises** — confirm
at the EID level which matches moved, never trust the aggregate delta (`§2.1`).

---

## Remaining source-ledger-derived candidates

These are candidates from the official-source ledger only. They are lower
priority than a current functional failure with source/corpus witnesses.

- **DONE** §6.9 non-textual modification — the manual-frontier classifier now gives
  `applied`/`excluded`/`modified`/`disapplied`/`restricted` a distinct
  out-of-scope-by-construction class (`uk_non_textual_modification_out_of_scope`)
  instead of the generic unsupported-effect-family lane; on ukpga/1978/30 that is
  85 of 109 `no_supported_action` rows. Classification only, replay-neutral.
- **DONE** §6.3.8 `words added`/`word added` lowered as a word-level insert (synonym
  of `words inserted`); broad similarity unchanged (word inserts edit text within
  existing nodes), the gain is source fidelity (the words now reach the text).
- **VERIFIED NOT A POPULATION** territorial-extent qualifiers (`(EW)`/`(S)`/`(NI)`)
  on dropped effect types — 0 in the sampled `no_supported_action` lane; no win.

- **DONE (sensor phase)** §6.8 prospective-only effects — a non-blocking sensor
  (`uk_prospective_effect_applied_to_current`, `prospective_effect_warrant.py`) now
  emits one observation per applied prospective-only structural effect (12 on
  ukpga/1998/17, 21 on ukpga/1978/30), so the population is visible and countable.
  Replay-neutral. The resolver phase (item 2 below) is what remains.

Remaining (each needs a verified failing case before building — do not add guards
for hypothetical bugs):

1. `UK_RULE_UNCOMMENCED_EFFECT_RESOLVER` (§6.8) — the PIT-aware *resolver* on top of
   the sensor: decide application per the oracle version / `authority_mode` instead of
   silently applying. Verified mixed-sign as a blanket gate, so it needs the version
   semantics modelled. Biggest remaining correctness lever; largest temporal change.
2. `UK_RULE_REPEAL_OF_REPEAL_NO_REVIVE` (§6.1.13) — still needs a concrete corpus
   witness before replay changes. `UK_RULE_REPEAL_NO_DOUBLE_ENTRY` (§6.1.5) is now
   implemented for exact body+Schedule duplicate structural repeals with diagnostic
   rejection records.
3. **DONE (diagnostic surface)** SI semantics from
   `STATUTORY_INSTRUMENT_PRACTICE_5TH_ED`: `scripts/uk_si_semantics_scan.py`
   now records commencement metadata/body clauses, vires/enabling-power recitals,
   extent/application candidates, revocation/lapse candidates, correction-slip
   markers, and SI structure vocabulary. It is deliberately replay-neutral; the
   next step is adjudicating a concrete family before changing lowering/replay.
4. **PARTIAL (UK branch graph prototype)** Proposed-law authority paths from
   OPC/Cabinet sources: `lawvm uk-branch-demo` now proves a UK-shaped
   proposed-law payload can be represented in the shared graph without leaking
   into enacted replay. Remaining work is real source acquisition/parsing for
   draft bills, carry-over, Parliament Act / Money Bill routes,
   financial-resolution provisional text, and Crown application.
5. The big EID divergence on the gate statute (ukpga/1978/30) is a Schedule-1
   **crossheading-representation** mismatch (`schedule-1-crossheading-…` +
   `_paragraph-wrapperNnM`), i.e. oracle editorial structure vs replay — saturated
   frontier, not a lowering gap.

Each needs the standard ownership package (`AGENTS.md §7/§15`): stable rule id,
finding/observation, strict-mode behaviour, synthetic + corpus + negative tests.

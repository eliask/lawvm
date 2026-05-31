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

## Downloaded-source audit (2026-05-31)

This audit reviewed the downloaded OPC, Cabinet Office/PBL, SI, and devolved
source bundle under `.tmp/uk_drafting_sources/` against the executable UK replay
surface. It is source-ledger evidence, not a replay mutation.

- **OPC Drafting Guidance 2024 remains the strongest executable source for
  explicit Act-amendment grammar.** Parts 6.1-6.7 and 6.9 support existing
  explicit repeal/omit, substitution, insertion, occurrence-scope, heading,
  schedule-scope, inserted-numbering, and non-textual-modification rule
  boundaries. They do not license fallback target broadening, action-family
  mutation, or heading/body substitution unless the source form says so.
- **OPC uncommenced/commencement/expiry material is temporal and conditional.**
  Parts 6.8, 10.6, and 10.7 support typed PIT/prospective/expiry lanes and
  manual-frontier classification. They do not support a blanket current-text
  prospective gate, and expiry must not be silently lowered as repeal.
- **Bill-amendment and process sources belong to proposed-law branches.**
  `OPC_AMENDING_BILLS_STYLE_MANUAL_2024` uses page/line amendment forms,
  printing/reprint conventions, motions, provisional numbering, and bill-stage
  "leave out and insert" forms. These are evidence for bill/proposed-law branch
  ingestion and editorial projection, not direct enacted-Act amendment lowering.
- **The Guide to Making Legislation is process authority, not an enacted-source
  oracle.** The electronic GOV.UK version is the live process source; PDF copies
  are captured renderings. Draft-bill publication, explanatory notes, legislative
  consent, amendment handling, Henry VIII powers, early commencement, and
  retrospective operation support source lanes, branch/version metadata, and
  diagnostics. They do not authorize replay mutation by themselves.
- **Explanatory notes and supporting papers are supporting witnesses.** They may
  be updated during passage and published alongside Acts, but they are not
  legislation and do not form part of the bill. Future acquisition may capture
  EN snapshots, but replay must not treat them as amendment authority.
- **SI Practice supports explicit SI temporal/address metadata, not broad
  fallback dates.** Table A supports type-conditioned SI address vocabulary, and
  explicit commencement date/time clauses can feed temporal lowering. Made-date
  defaults, approval/consent conditions, lapse from parent repeal, correction
  slips, and reprints remain diagnostic/manual-frontier unless a concrete source
  witness owns the recovery.
- **Extent and application remain distinct metadata dimensions.** OPC and Guide
  sources reinforce that extent is the jurisdiction whose law is changed while
  application is the persons/matters/place affected. This is a no-widening
  invariant, not a tree mutation.
- **Downloaded devolved/toolbox material is incomplete.** The SI/toolbox HTML
  captures are mostly stubs or login/index pages. The Scotland `Drafting
  Matters` capture exposes useful metadata and a promising table of contents, but
  not enough operative text for compiler rules. Treat this as an acquisition
  follow-up, not a semantic rule source.

Result: no new replay-changing rule is justified solely by this downloaded-source
pass. The useful immediate outputs are stronger source citations, acquisition
frontier classification, and rule-boundary documentation.

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
  Result: 4,869 SI-like current XML documents scanned, 41,653 diagnostic rows:
  4,869 structure-vocabulary rows, 291 commencement-default rows, 4,863
  vires-recital rows, 4,578 commencement-metadata rows, 7,674 body-commencement
  clause rows, 2,398 temporal-effect rows, 3,214 extent rows, 10,777 application
  rows, 2,750 revocation/lapse rows, and 239 correction/reprint context rows.
  Body-clause rows now include `source_role`, and
  the all-cached scan classifies 2,625 rows as `payload_carried` because they sit
  inside amendment payload XML rather than the SI's own body provision. Body-clause
  records also expose `geographic_terms` and `extent_application_relation`; current
  summary counts are 12,422 `application_only`, 3,792 `extent_only`, and 2,044
  `combined_extent_and_application` rows. Revocation/lapse rows expose
  `revocation_lapse_kinds`; current marker counts are 3,735 `revocation`, 803
  `cessation`, and 323 `lapse`. Temporal-effect rows expose
  `temporal_effect_clause_kinds`; current marker counts are 612 `appointed_day`,
  153 `specified_day`, 1,083 `relative_to_made_day`, 2,799 `on_or_after_date`,
  95 `continuation_period`, and 2,366 `calendar_date_text`. Vires-recital rows
  expose `vires_markers` and bounded `citation_texts`; current marker counts are
  3,048 `exercise_of_powers`, 3,124 `powers_conferred`, 365 `designation`, 177
  `consultation`, and 1,364 `approval`. Correction rows now record direct
  element/attribute contexts rather than whole-document text, expose
  `correction_marker_kinds`, and currently count 235 `correction_slip` markers
  and 5 `reprint` markers. Structure rows now expose the SIP Table A expected
  body-unit vocabulary for mapped minor types: 2,687 `article` order rows, 2,056
  `regulation` rows, and 108 `rule` rows; 18 rows remain unmapped/unknown
  (`unknown`, empty, `scheme`, or `resolution`). Commencement-default rows record
  no-`ComingIntoForce` instruments separately from replay fallback: 287 expose a
  single `Made/@Date` as a SIP §3.12 default-commencement candidate, while 4 have
  no made date and remain unresolved. Of the single-made-date candidates, 271 have
  body commencement clauses and are flagged
  `body_commencement_clause_needs_adjudication`; 13 have no explicit
  commencement clause but do have body temporal-effect clauses and are flagged
  `body_temporal_effect_clause_needs_adjudication`; only 3 currently have
  `no_body_commencement_or_temporal_clause_seen`. Body commencement rows now expose
  `commencement_clause_kinds`; all-SI marker counts include 7,893
  `operative_comes_into_force`, 60 `operative_comes_into_operation`, 454
  `citation_commencement_title`, 1,835 `relative_to_made_day`, 287
  `appointed_day_text`, and 8,086 `calendar_date_text`. These are evidence rows only;
  replay-changing SI rules still require source-level adjudication of a concrete
  family.
  **Commencement fallback adjudication added 2026-05-31:** applied UK SI effects
  with no effect-level in-force date still recover a replay date only from exactly
  one official `ComingIntoForce/DateTime/@Date`. Missing source XML, parse errors,
  made-date default candidates, textual-only commencement metadata, and multi-date commencement metadata now emit
  nonblocking `uk_effect_undated_applied_si_commencement_unresolved` observations
  instead of disappearing as an invisible non-recovery. On the 77-statute broad
  gate this exposes 312 unresolved non-recoveries (295
  `default_commencement_made_date_candidate`, 17 `multiple_or_textual`) alongside
  321 existing single-date source-backed overrides. Made-date default candidates
  record `commencement_metadata_made_dates` and `commencement_default_candidate`
  but are not used as replay dates.

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
  **Structured import added 2026-05-31:** `lawvm uk-branch-import <payload.json>`
  imports an explicit proposed-law claim JSON into the same branch graph lane.
  This is an owned-claim boundary, not a parser: absent a real draft/proposed bill
  source artifact, LawVM still refuses to infer proposed operations from guidance
  documents or demo prose.

---

## Mined rules → LawVM destinations (OPC Drafting Guidance Part 6)

Status legend: **HAVE** (implemented, verify), **GAP** (not handled / partial),
**SPEC** (diagnostic/spec-yield, no replay).

Implementation status is a snapshot, not an authority claim. Before acting on a
`GAP` or `HAVE`, verify current code and current corpus behavior; this file is
allowed to lag the executable implementation.

### §6.1 Repeals
- **6.1.2 operative form** `In the [Act] omit section 10 / omit Part 2`; whole-Act
  `The [Act] is repealed`. → UK repeal lowering. **HAVE (verified)** for
  explicit whole-Act repeal text: lowering preserves the effect-feed `repealed`
  action as one `REPEAL` operation targeting `/whole_act`. Partial whole-Act
  repeal with exceptions is not compiled as a blanket repeal; it blocks under
  `uk_effect_partial_whole_act_repeal_rejected`. Witnesses:
  `test_compile_preserves_explicit_whole_act_repeal_effect_type` and
  `test_compile_rejects_partial_whole_act_repeal_scope`.
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
  for no-revive / repeal-of-repeal phrases. The selected-source scan on the
  77-statute gate has no direct no-revive phrase witnesses. A fast direct
  source-phrase lane now exists:
  `uv run python scripts/uk_repeal_semantics_scan.py --all --source-phrase-only
  --pretty --limit 0`. Current local all-archive result: 19,295 current XML
  documents scanned, 34 source-phrase witnesses, including 7
  `repeal_of_repeal_no_revive_phrase` witnesses and 27 `repeal_revival_phrase`
  witnesses. A bounded middle lane now links phrase-bearing affecting Acts to
  repeal-family effect rows without selected-source extraction:
  `uv run python scripts/uk_repeal_semantics_scan.py --ids-file
  scripts/baselines/uk_grounding_corpus.txt --source-phrase-effect-candidates
  --phrase-all --pretty --limit 0`. Current local result: 77 affected statutes,
  19,315 phrase-source documents scanned, 27 phrase-bearing Acts, and 20 linked
  candidate effect rows (10 no-revive, 10 revival). These rows prove an effect
  uses a phrase-bearing affecting Act, but not that the phrase is in the selected
  source provision or that the target is itself a repeal. The candidate lane now
  has `--audit-selected-source` to resolve the selected source provision for each
  candidate and report `selected_source_matches_phrase`. Focused witness:
  `ukpga/1992/41` affected by `ukpga/2006/50` has 9 no-revive candidates, but the
  selected sources are absent or `Sch. 9` repeal-table surfaces with
  `selected_source_matches_phrase=false`, so they remain unproved candidates. Do
  not add a replay guard until a selected source/target witness is proved.
- **6.1.14 repealing a paragraph with a trailing conjunction** — make the `and`/`or`
  explicit. → connects to existing `tail_connector` modelling. **HAVE (verified)**
  for bounded text deletion/split lanes: final-word forms such as `the word "or"
  at the end of paragraph (a) is repealed` lower with `occurrence=-1`, and mixed
  structural+word repeal rows split the contextual conjunction deletion from the
  structural child repeal. These lanes delete only the source-named connector
  text and must not delete neighbouring children by coincidence. Witnesses:
  `test_compile_additional_frontier_text_patch_idioms`,
  `test_compile_contextual_preceding_word_repeal_uses_adjacent_anchor`, and
  `test_compile_repeal_table_mixed_structural_and_word_repeal_splits_ops`.

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
  heading-facet selector. **HAVE (verified)** for the narrow source form
  `for "X" (including in the heading) substitute "Y"` via
  `uk_effect_mixed_body_heading_substitution_split_text_patch`; lowering emits
  separate body and heading-facet text patches rather than widening either
  target. Witness:
  `test_compile_mixed_body_heading_text_substitution_splits_heading_facet`
  (`ukpga/1998/17` `s. 9B`, amended by `ukpga/2016/20` `Sch. 1 para. 8(a)`).

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
  **2026-05-31 witness surface:** `scripts/uk_prospective_commencement_scan.py`
  classifies prospective-only structural effects against affecting-provision
  `RestrictStartDate` without changing replay. Current 77-statute command:
  `uv run python scripts/uk_prospective_commencement_scan.py --ids-file
  scripts/baselines/uk_grounding_corpus.txt --as-of 2026-05-31 --pretty --limit
  20`. Result: 325 prospective-only structural witnesses; 185 resolved in-force,
  140 unresolved, 0 resolved future. This gives the future PIT resolver an
  auditable workqueue while confirming that a current-corpus blanket gate would
  still be unsupported. **PIT-only resolver hook added:** when `compile_ops_for_statute`
  is called with a `pit_date`, a prospective-only structural effect now consults
  the affecting provision's `RestrictStartDate`: resolved in-force effects are kept
  with `uk_effect_pit_prospective_commencement_in_force`, resolved future effects
  are filtered with `uk_effect_pit_prospective_commencement_future_rejected`, and
  unresolved effects emit `uk_effect_pit_prospective_commencement_unresolved` before
  falling through to existing PIT filtering. Default current replay is unchanged.

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
   lens. **No-double-entry / no-revive (§6.1)** now have diagnostic witness
   surfaces. No-revive has phrase-bearing-affecting-Act candidate effect rows, but
   still lacks a selected source/target witness for a replay-changing guard.

Verification for any of these uses the broad baseline, not the 9-statute gate:
`scripts/uk_broad_baseline.py --ids $(cat scripts/baselines/uk_grounding_corpus.txt)
--out .tmp/after.json` then `--compare scripts/baselines/uk_broad_2026-05-31.json
.tmp/after.json`. A score *drop* that corresponds to removing an over-applied or
spuriously-grounded match is a **correctness gain the guard mis-penalises** — confirm
at the EID level which matches moved, never trust the aggregate delta (`§2.1`).

**Wider-corpus source-frontier refinement (2026-05-31):**
`scripts/uk_broad_baseline.py` now classifies both enacted and oracle/current XML
before parsing. Too-small, parse-error, and oracle metadata-only blobs are emitted
as `score_status=source_frontier` rows rather than hard errors or trivial replay
scores. Witness: `ukpga/1945/9`, whose enacted and current cached blobs are both
`HTTP 300 Multiple Choices` (25 bytes), now reports
`source_frontier_reason=base_too_small`. A 160-statute post-fetch sample
(`--sample 160 --seed 31`) reports 108 scored rows, 52 source-frontier rows,
0 errors, mean aligned `72.98%`, mean aligned_no_gc `77.70%`, and 4,216
grounding-collateral eIds. Empty oracle-eId rows remain scored because they can
represent real over-retention/missing-repeal evidence: `ukpga/1938/22` currently
has `NumberOfProvisions=104`, all current provisions ended/repealed, and replay
still retains 420 eIds. The broad-baseline summary now prints
`source_frontier_reason` counts and zero-oracle retention totals explicitly;
the `ukpga/1938/22` / `ukpga/1945/9` witness run reports
`zero_oracle_retention=1 rows / 420 replay eIds` and
`source_frontier_reasons: base_too_small=1`. The broad-baseline summary also
prints a non-scoring `triage_bucket` on each row plus aggregate bucket counts so
raw score rank does not confuse grounding/style/frontier classes with
deterministic replay bugs. On the curated 77-statute gate the current split is
`high_fidelity_after_grounding=49`, `grounding_dominated_residual=10`,
`structural_match_eid_scheme_residual=4`, `base_metadata_only_frontier=1`, and
`residual_after_grounding=13`. A later refinement splits rows with no compiled
operation stream into `no_compiled_ops_frontier=7`, leaving
`residual_after_grounding=10` active replay/source-family selectors. The gate
remains unchanged (`0 improved, 0 regressed`), with 77/77 scored and
`source_frontier=0`.

**Residual work-selection refinement (2026-05-31):**
The broad baseline now records row-level compile diagnostics, blocking compile
barriers, rule histograms for both, and aligned miss-side counts
(`n_only_in_oracle` / `n_only_in_replayed`). Evidence collection is deliberately
isolated in a second diagnostic compile because some UK compiler diagnostic paths
are list-present sensitive; replay scoring remains on the historical no-output
compile path. Triage uses **blocking** compile barriers, not successful
observations such as text patches, date recoveries, or eId synthesis. The 10
previously active `residual_after_grounding` rows now split without replay
mutation into:

- `compile_rejection_dominated_residual=3`: `ukpga/1984/12`, `ukpga/1968/20`,
  `ukpga/1990/8`.
- `bounded_low_volume_residual=2`: `ukpga/1997/7`, `ukpga/1976/38`.
- `residual_after_grounding=5`: `ukpga/1986/61`, `eur/2019/1021`,
  `ukpga/1981/20`, `ukpga/1998/17`, `ukpga/1990/9`.

Current 77-statute baseline snapshot:
`scripts/baselines/uk_broad_2026-05-31.json`. It scores 77/77, mean aligned
`93.53%`, mean aligned_no_gc `93.53%`, grounding-collateral `0`,
metadata-only base `1`, errors `0`, source-frontier `0`, and compare against
itself is `0 improved, 0 regressed`. Current bucket split:
`high_fidelity_after_grounding=57`, `manual_compile_frontier_residual=13`,
`no_effect_rows_frontier=4`, `nonreplay_effect_frontier=2`, and
`base_metadata_only_frontier=1`. The source-chain frontier split is now
`effect_rows_absent_or_unpublished=4` (`ssi/2025/74`, `ukpga/1976/83`,
`ukpga/2011/2`, `uksi/2012/1206`) and
`effect_rows_missing_structural_payload=2` (`ukpga/1901/7`,
`uksi/2009/3023`), with no remaining `effect_feed_pages_absent` rows after
refreshing stale current/effects source surfaces. The older 2026-05-30 snapshot
is retained as historical context; after the wider-corpus fetch it differs on
`ukpga/1990/8` because the current oracle eId surface in the local farchive
changed (`oracle=8180` in the old snapshot, `oracle=8218` now).

**Broad comparison surface correction (2026-05-31):**
`scripts/uk_broad_baseline.py` now compares replay against the same normalized
legal-identity lens as `lawvm uk-misses`: oracle ids come from
`extract_eid_map_bytes(...).eid_map`, and both replay/oracle sets pass through
`normalize_uk_replay_compare_eids` with physical-id and visible-number aliases.
The old broad scorer used raw parsed current-tree eIds, so it could disagree
with direct miss inspection by counting transport/text-fragment or extractor-only
identity noise. Witness: `ukpga/1966/42` was a false broad residual (`91.49%`,
`bounded_low_volume_residual`) while `uk-misses` was already perfect; after this
correction the broad row is `100.00%` and `high_fidelity_after_grounding`. The
77-statute corrected snapshot
`.tmp/uk_broad_after_broad_compare_normalization_20260531.json` scores 77/77,
mean aligned `93.38%`, mean aligned_no_gc `93.38%`, grounding-collateral `0`,
metadata-only base `1`, errors `0`, source-frontier `0`. The mean moves from the
previous local post-fix `94.21%` to `93.38%` because this is a scoring-surface
correction, not a replay mutation; rows such as `ssi/2025/74`,
`uksi/2009/3023`, and `uksi/2012/1206` now expose extra oracle legal ids and
correctly sit in `effect_feed_absent_frontier` instead of being hidden by the old
raw parsed surface.

The broad triage also separates small residuals whose missing oracle ids are
already explained by blocking manual-frontier compile records into
`manual_compile_frontier_residual`. Witnesses in the corrected 77-statute gate:
`ukpga/1887/55` and `ukpga/1968/70` have only oracle-side misses and blocking
table/source-payload records; they are not ordinary bounded residuals unless a
new source-owned compiler family is identified. Verification snapshot
`.tmp/uk_broad_after_manual_frontier_bucket_20260531.json` remains score-stable
at 77/77 scored, mean aligned `93.38%`, errors `0`, source-frontier `0`; bucket
split is `manual_compile_frontier_residual=2`,
`compile_rejection_dominated_residual=3`, `residual_after_grounding=6`, and
`high_fidelity_after_grounding=57` plus existing source/frontier buckets.

**Manual-frontier diagnostic surface correction (2026-05-31):**
`scripts/uk_broad_baseline.py` now collects the compiler's
`uk_manual_compile_frontier_classified` diagnostics on the broad row itself:
`manual_frontier_status_counts`, `manual_frontier_rule_counts`, and
`manual_frontier_template_status_counts`. Triage now uses this authoritative
manual/source-frontier stream rather than inferring only from blocking lowering
rule names. The bucket is a workqueue selector, not a claim that every oracle-only
eId is explained one-for-one by a manual item; it says the remaining row-level
frontier is already routed through manual/source/out-of-scope diagnostics rather
than unclassified replay mutation. Verification snapshot
`.tmp/uk_broad_after_manual_frontier_diagnostics_20260531.json` is score-stable
against the previous local snapshot: 77/77 scored, mean aligned `93.38%`, errors
`0`, source-frontier `0`, and no replay/oracle count changes. Bucket split:
`high_fidelity_after_grounding=57`, `manual_compile_frontier_residual=11`,
`effect_feed_absent_frontier=5`, `no_effect_rows_frontier=2`,
`nonreplay_effect_frontier=1`, `base_metadata_only_frontier=1`. The 11
manual-frontier residual rows are `eur/2019/1021`, `ukpga/1887/55`,
`ukpga/1968/20`, `ukpga/1968/70`, `ukpga/1980/65`, `ukpga/1981/20`,
`ukpga/1984/12`, `ukpga/1986/61`, `ukpga/1990/8`, `ukpga/1990/9`, and
`ukpga/1998/17`. Current full-gate compiler totals are
`manual_compile_candidate=161`, `deterministic_frontend_candidate=8`,
`source_insufficient=213`, `deterministic_frontend_supported=5678`,
`non_textual_or_out_of_scope=8407`, and `source_or_feed_target_conflict=2`.
The broad summary now aggregates those counts directly and also aggregates
`manual_frontier_template_status_counts`. A parser/extraction gap template was
added for `uk_manual_frontier_parser_or_extraction_candidate`, so all actionable
manual/deterministic candidate work items now have claim-template coverage:
`available=169`, `none=14300`, `manual_frontier_template_gaps=0`. The broad
driver supports `--fail-on-manual-frontier-template-gaps` to make that coverage a
machine-enforced gate. The broad summary also
prints `active_unclassified_residuals`; this must stay at `0` for the curated
77-statute gate if the UK frontend is to remain at the "done modulo
manual/source frontiers" line. Any nonzero row is a regression in workqueue
classification or a new deterministic family to investigate. Use
`scripts/uk_broad_baseline.py --fail-on-active-unclassified-residuals` when this
condition should be machine-enforced rather than inspected from summary text.
Full-gate enforcement witness:
`uv run python scripts/uk_broad_baseline.py --ids $(cat
scripts/baselines/uk_grounding_corpus.txt)
--fail-on-active-unclassified-residuals --out
.tmp/uk_broad_current_fail_guard_20260531.json` exited `0`, scored 77/77,
mean aligned `93.38%`, errors `0`, grounding-collateral `0`, and
`active_unclassified_residuals=0`. The triage split was
`high_fidelity_after_grounding=57`, `manual_compile_frontier_residual=11`,
`effect_feed_absent_frontier=5`, `no_effect_rows_frontier=2`,
`nonreplay_effect_frontier=1`, and `base_metadata_only_frontier=1`; source-chain
frontiers were `effect_feed_pages_absent=5`,
`effect_rows_absent_or_unpublished=2`, and
`effect_rows_missing_structural_payload=1`.
Template-coverage guard witness:
`uv run python scripts/uk_broad_baseline.py --ids $(cat
scripts/baselines/uk_grounding_corpus.txt)
--fail-on-active-unclassified-residuals
--fail-on-manual-frontier-template-gaps --out
.tmp/uk_broad_after_template_gap_guard_20260531.json` exited `0`, kept
`active_unclassified_residuals=0`, and printed
`manual_frontier_template_gaps=0`. Compare against the immediately prior current
snapshot `.tmp/uk_broad_current_fail_guard_20260531.json` was `0 improved,
0 regressed`.

**Replay adjudication JSON surface correction (2026-05-31):**
`uk-replay --json` now includes `replay_adjudication_bucket_counts` alongside
`adjudication_kind_counts`, including an explicit empty `{}` when no replay
adjudications are present. This aligns the replay JSON contract with the
existing `uk-replay` text output, `uk-bench`, `uk-candidates`, and evidence
bundle/review surfaces, so operator triage can distinguish replay-bug,
source-shape, text-surface, and nonblocking observation lanes without
recomputing classifier buckets downstream.
Focused verification: `uv run python scripts/uk_broad_baseline.py --ids
ukpga/1990/8 ukpga/1986/61 ukpga/1887/55 --out
.tmp/uk_broad_after_replay_adjudication_payload_surface_20260531.json` keeps
all three sampled residual rows in `manual_compile_frontier_residual` and prints
`active_unclassified_residuals=0`.

**Oracle-alignment fallback suppression (2026-05-31):**
Current UK oracle alignment no longer writes unmatched local fallback eIds into
the replay tree. When a replay node cannot be matched to an oracle id by hash,
text, flat/path, or ordinal evidence, the adapter leaves the node unlabeled and
emits `local_fallback_suppressed` with the would-be fallback id in `match_key`.
This is an identity-adapter rule, not a legal-state mutation: source-owned replay
and payload-normalization eIds remain valid, but the oracle adapter may not
invent ids for nodes the oracle does not identify. Historical saved runs that
contain `local_fallback` remain interpretable through the collateral-excluded
score lane. Broad gate
`.tmp/uk_broad_after_fallback_suppression_20260531.json` scored 77/77 with mean
aligned `92.45%`, mean aligned_no_gc `92.45%`, grounding-collateral `0`,
metadata-only base `1`, errors `0`, source-frontier `0`; compare against
`scripts/baselines/uk_broad_2026-05-31.json` = `30 improved, 0 regressed`.

**Retained-EU annex identity idempotence (2026-05-31):**
The remaining zero-op structural eId-scheme witnesses `eur/2019/2018` and
`eur/2019/1746` showed that enacted/current parsing already agreed exactly, but
oracle alignment made them worse by clearing canonical annex schedule-entry ids,
generating local child ids below schedule-entry containers, and allowing
editorial footnote ids (`f000xx`) into hash grounding candidates. The adapter now
preserves schedule-entry ids already present in the current-source oracle map,
recurses through their descendants to suppress unproved child ids, keeps
non-oracle UK-generated schedule entries non-public, and excludes `Footnote`
from eId extraction. Spot checks after the fix:

- `eur/2019/2018`: aligned `100.0%`, unaligned `100.0%`, replay/oracle eIds
  `62/62`, replay-only `0`, oracle-only `0`.
- `eur/2019/1746`: aligned `100.0%`, unaligned `100.0%`, replay/oracle eIds
  `61/61`, replay-only `0`, oracle-only `0`.

Broad gate `.tmp/uk_broad_after_retained_eu_identity_20260531.json` scored
77/77 with mean aligned `93.87%`, mean aligned_no_gc `93.87%`,
grounding-collateral `0`, metadata-only base `1`, errors `0`, source-frontier
`0`. Compare against the fallback-suppression run:
`3 improved, 0 regressed` (`eur/2019/2018`, `eur/2019/2013`,
`eur/2019/1746`). Compare against `scripts/baselines/uk_broad_2026-05-31.json`:
`30 improved, 0 regressed`. Current bucket split:
`high_fidelity_after_grounding=57`, `residual_after_grounding=6`,
`bounded_low_volume_residual=5`, `compile_rejection_dominated_residual=3`,
`no_compiled_ops_frontier=5`, and `base_metadata_only_frontier=1`.

**Effect-feed absent frontier split (2026-05-31):**
The broad-baseline triage now separates rows with `n_ops=0` because the archive
has no effect-feed pages (`uk_effect_feed_pages_absent_recorded`) from generic
no-compiled-op rows. This is a source/effect-feed frontier, not a lowering miss.
The replay scores are unchanged (`0 improved, 0 regressed` against
`.tmp/uk_broad_after_retained_eu_identity_20260531.json`). Current gate
`.tmp/uk_broad_after_effect_feed_frontier_20260531.json` still scores 77/77 with
mean aligned `93.87%`, mean aligned_no_gc `93.87%`, grounding-collateral `0`,
metadata-only base `1`, errors `0`, source-frontier `0`; bucket split:
`high_fidelity_after_grounding=57`, `residual_after_grounding=6`,
`bounded_low_volume_residual=5`, `compile_rejection_dominated_residual=3`,
`no_compiled_ops_frontier=3`, `effect_feed_absent_frontier=2`, and
`base_metadata_only_frontier=1`. Current witnesses:
`uksi/2000/1043`, `uksi/2010/1504`.

**No-op frontier split (2026-05-31):**
The broad-baseline triage now records `n_effects` and splits the remaining
generic zero-op bucket. Rows with no archived effect rows become
`no_effect_rows_frontier`; rows with effect rows that compile only to
non-replay/nonstructural observations become `nonreplay_effect_frontier`. This
again changes work selection only; compare against
`.tmp/uk_broad_after_effect_feed_frontier_20260531.json` is
`0 improved, 0 regressed`. Current gate
`.tmp/uk_broad_after_noop_frontier_split_20260531.json` scores 77/77 with mean
aligned `93.87%`, mean aligned_no_gc `93.87%`, grounding-collateral `0`,
metadata-only base `1`, errors `0`, source-frontier `0`; bucket split:
`high_fidelity_after_grounding=57`, `residual_after_grounding=6`,
`bounded_low_volume_residual=5`, `compile_rejection_dominated_residual=3`,
`effect_feed_absent_frontier=2`, `no_effect_rows_frontier=2`,
`nonreplay_effect_frontier=1`, and `base_metadata_only_frontier=1`. Current
witnesses: `no_effect_rows_frontier` = `ukpga/1976/83`, `ukpga/2011/2`;
`nonreplay_effect_frontier` = `ukpga/1901/7`.

**Single unnumbered Schedule extraction recovery (2026-05-31):**
`ssi/2006/536` exposes its first source Schedule as unnumbered `schedule` while
also exposing numbered `schedule-2` and `schedule-3`; the effect feed cites the
first source row as `Sch. 1 para. 8`. LawVM now accepts `Sch. 1 para. X` as
`Sch. para. X` only when there is exactly one visibly unnumbered Schedule and
the requested schedule label is `1`, emitting
`uk_affecting_act_single_unnumbered_schedule_context_ignored`. This recovered
the source-owned payload for `ukpga/1976/38` s. 6(3A) without admitting a broad
Schedule payload or rebinding any `Sch. 2+` reference. Broad gate result:
`ukpga/1976/38` improved `91.92 -> 92.93`, 0 regressions.

**Non-textual/no-op lowering demotion (2026-05-31):**
No-supported-action diagnostics remain visible, but source-pathology classes
that prove a row is outside direct text/tree replay now reclassify those
diagnostics as nonblocking. This includes `nonstructural_root_gap` and
`application_by_reference_effect_out_of_scope`; the latter now recognizes
source clauses such as “has effect for the purpose of the application of...”.
Current witness: `ukpga/1997/7`, whose three blocking rows are application or
nonstructural rows from `ukpga/2000/11` Sch. 15 para. 15, not deterministic
text/tree mutations. After the demotion `ukpga/1997/7` has zero blocking compile
rejections while preserving its miss rows. Broad gate scores are unchanged
(`0 improved, 0 regressed`), and the active bucket split moves one row from
`compile_rejection_dominated_residual` to `residual_after_grounding`.

**Passive quoted substitution lowering (2026-05-31):**
Source clauses of the form `for "X", there shall be substituted "Y"` now emit
the named text-patch observation
`uk_effect_passive_quoted_substitution_text_patch` instead of relying on an
unnamed fragment parse or blocking as overlap substitution. The related
exception-bearing form `for "X" (except in the phrase "Y"), there shall be
substituted "Z"` emits `uk_effect_except_phrase_substitution_text_patch`; replay
preserves the excluded phrase in the selector rather than applying an unsafe
all-occurrences rewrite. Current witness: `ukpga/1984/12`, affected by
`uksi/2003/2155` Sch. 1 para. 1(2)(a)-(e), where five previously blocked
source-owned text substitutions now lower to typed text patches. The refreshed
77-statute gate remains score-stable (`0 improved, 0 regressed`), while
`ukpga/1984/12` blocking compile diagnostics drop `47 -> 42` and
`ukpga/1998/17` drops `19 -> 18`.

The same exception-bearing selector now also covers all-occurrences clauses
that exclude an explicit expression, e.g. `for the word "X", wherever occurring
(otherwise than in the expression "Y"), there shall be substituted "Z"`.
Witness: `ukpga/1984/12` affected by `ukpga/2003/21` Sch. 3 para. 5(d), where
the effect-feed range `Sch. 2 para. 2-28` expands to 27 target-local text
patches using `TEXT_EXCEPT_PHRASE`. This removes the matching
`uk_effect_overlap_substitution_unlowered` blocker without mutating the excluded
phrase. The refreshed 77-statute gate remains score-stable (`0 improved,
0 regressed`), while `ukpga/1984/12` blocking compile diagnostics drop
`42 -> 41`.

Child-scope exclusions are distinct from phrase exclusions. Source clauses such
as `for the words "X", wherever occurring, except in subsection (9), there shall
be substituted "Y"` now emit `uk_effect_except_child_substitution_text_patch`
with a `TEXT_EXCEPT_CHILD` selector. Replay requires the excluded child to exist
and skips that child subtree; it does not treat the missing child as permission
to rewrite every occurrence. Current witness: `ukpga/1984/12`, affected by
`ukpga/2003/21` Sch. 17 para. 67(2). This removes the remaining
`uk_effect_overlap_substitution_unlowered` blocker for `ukpga/1984/12`; blocking
compile diagnostics drop `41 -> 40`, and the 77-statute gate remains
score-stable (`0 improved, 0 regressed`).

Quoted-anchor insertions tolerate publisher whitespace before the separator
comma under `uk_effect_after_quoted_anchor_space_before_comma_insert_text_patch`.
This covers ordinary source text such as `after "X" , insert "Y"` without
treating the space as legal payload. Current witness: `ukpga/1990/8` affected
`s. 264(4)(a)` by `uksi/2012/1659` Sch. 3 para. 9. The row now lowers to the
same target-local text patch as `after "X", insert "Y"` with a separate
observation for the punctuation recovery; `ukpga/1990/8` blocking compile
diagnostics drop `135 -> 134`, and the 77-statute gate remains score-stable
(`0 improved, 0 regressed`).

At-end insertions also accept a quoted payload after a dash separator under
`uk_effect_at_end_quoted_dash_text_insertion_patch`, e.g. `at the end insert-
"Y"`. The dash is instruction punctuation; the quoted payload is appended to
the explicit affected target. Current witness: `ukpga/1990/8` affected
`s. 1(2)` by `ukpga/2007/24` s. 31(1). This cuts `ukpga/1990/8` overlap
blockers `16 -> 15` and total blocking compile diagnostics `134 -> 133`; the
77-statute gate remains score-stable (`0 improved, 0 regressed`).

Quoted substitutions may carry a parenthetical scope note between the quoted
preimage and the `substitute` verb. The lowering rule
`uk_effect_quoted_substitution_scope_note_text_patch` records that note as
source evidence and emits only the quoted preimage/replacement as the executable
target-local text patch; it does not treat the parenthetical as legal payload or
as authority to widen the target. Current witness: `ukpga/1990/8` affected
`s. 323(2)` and `s. 323(3)` by `wsi/2014/2773` Sch. 1 para. 11(3), where the
source says `for "The regulations may" (in so far as those words continue to
form part of those subsections) substitute "Regulations under this section may"`.
This cuts `ukpga/1990/8` overlap blockers `15 -> 13`, blocking effect rows
`132 -> 130`, and blocking compile diagnostics `133 -> 131`; the 77-statute
gate remains score-stable (`0 improved, 0 regressed`).

Ordinal block inserts after a quoted anchor are owned under
`uk_effect_after_quoted_anchor_ordinal_block_insert_text_patch` for source rows
shaped `after "X" in the Nth place insert - Y`. The rule preserves the ordinal
occurrence and treats the dash payload as inserted text, matching the existing
quoted ordinal-insert and unquoted block-insert families without converting
quoted payloads away from their older rule. Current witnesses: `ukpga/1990/8`
affected by `ukpga/2015/7` Sch. 4 paras. 6, 7, 9(2), and 10(a)-(b). This cuts
`ukpga/1990/8` overlap blockers `13 -> 8`, blocking effect rows `130 -> 125`,
and blocking compile diagnostics `131 -> 126`; the 77-statute gate remains
score-stable (`0 improved, 0 regressed`).

Closing-quote inserted payloads are treated as a source quotation pathology only
under `uk_effect_after_quoted_anchor_closing_quote_insert_text_patch`, e.g. when
source says `after "X" insert ", Y,"` but encodes the inserted payload's opening
quote as `”`. The repair records the pathology and emits the ordinary
target-local quoted-anchor insertion; it does not broaden the quoted anchor
grammar. Current witness: `ukpga/1990/8` affected by `anaw/2015/4`
s. 43(4)(b). This cuts `ukpga/1990/8` overlap blockers `8 -> 7`, blocking
effect rows `125 -> 124`, and blocking compile diagnostics `126 -> 125`; the
77-statute gate remains score-stable (`0 improved, 0 regressed`).

Dangling-quote inserted payloads are treated as a separate source quotation
pathology only under `uk_effect_after_quoted_anchor_dangling_insert_quote_text_patch`,
when source says `after "X" insert "Y` and the inserted payload is bounded by
the end of the extracted instruction. The repair records `source_text_recovery`
evidence and emits a target-local quoted-anchor insertion; it does not infer a
closing quote from live text or escalate to a host replacement. Current witness:
`ukpga/1990/8` affected `s. 62C(3)` by `ukpga/2017/20` s. 2(12). This cuts
`ukpga/1990/8` overlap blockers `7 -> 6`, blocking effect rows `124 -> 123`,
and blocking compile diagnostics `125 -> 124`; the 77-statute gate remains
score-stable (`0 improved, 0 regressed`).

Bracket-span insertions are owned as a bounded text selector under
`uk_effect_after_words_in_brackets_insert_text_patch` when source says `after
the words in brackets insert- Y`. Replay inserts immediately after the unique
parenthesized span in the explicit effect target; ambiguous or missing bracket
spans remain blocked and the rule does not infer the bracket text from the live
target during lowering. Current witness: `ukpga/1990/8` affected `s. 1(5)(c)`
by `uksi/2024/49` reg. 4. This cuts `ukpga/1990/8` overlap blockers `6 -> 5`,
blocking effect rows `123 -> 122`, and blocking compile diagnostics `124 -> 123`;
the 77-statute gate remains score-stable (`0 improved, 0 regressed`).

Range substitutions may carry occurrence wording on either range anchor. The
existing range selector family now accepts plural start-anchor wording such as
`from "X", where they first occur, to "Y" substitute "Z"` and end-anchor
wording such as `to "Y", in the second place it occurs, substitute "Z"`. These
lower to `TEXT_FROM_X_TO_Y` with `occurrence` and/or independent
`end_occurrence`; replay must use the named occurrence rather than broadening to
the first matching range. Current witnesses: `ukpga/1990/8` affected `Sch. 1
para. 5(2)` and `Sch. 1 para. 5(3)` by `ukpga/2023/55` Sch. 17 para. 2(7)(a)
and (b), and `eur/2019/1021` affected `Annex 1 Pt. A Table` by `uksi/2022/1293`
reg. 2(2)(b)(i), whose source uses `to "Commission", in the first place it
occurs`. This cuts `ukpga/1990/8` overlap blockers `5 -> 3`, blocking effect
rows `122 -> 120`, and blocking compile diagnostics `123 -> 121`; the broad
gate additionally lowers one `eur/2019/1021` row from the same family and
remains score-stable (`0 improved, 0 regressed`).

Definition-child before-anchor insertions are source-owned when the row names
the definition term, the child label, and the quoted anchor inside that child.
The parser lowers `in the definition of "D" at the end of paragraph (a), before
the "and" insert "X"` to a bounded `TEXT_IN_DEFINITION_CHILD_PARAGRAPH_D/a/and`
text replacement under `uk_effect_in_definition_child_before_anchor_insert_text_patch`.
Replay may replace only the matching definition child text; it must not rewrite
the host subsection or every `and` in the target. Current witness:
`ukpga/1990/8` affected `s. 336(1)` by `uksi/2018/1232` reg. 2(3). This cuts
`ukpga/1990/8` overlap blockers `3 -> 2` and blocking compile diagnostics
`121 -> 120`; the 77-statute gate remains score-stable (`0 improved,
0 regressed`).

Deictic child-tail omissions are source-owned when the current source row says
`omit the words after that paragraph` and the immediate previous source sibling
under the same source parent explicitly identifies the paragraph target. The
lowering rule `uk_effect_source_carried_deictic_child_tail_repeal_text_patch`
records `source_deictic_antecedent=previous_source_sibling` and emits the same
bounded `TEXT_AFTER_CHILD_TAIL_paragraph_<label>` selector family. It may not
infer the paragraph from live text, skip unrelated siblings, or broaden the feed
target beyond an exact subsection. Current witness: `ukpga/1990/8` affected
`s. 74(1B)` by `ukpga/2016/22` s. 149(2)(c), whose previous sibling
s. 149(2)(b) names paragraph `(c)`. This cuts `ukpga/1990/8` overlap blockers
`2 -> 1` and blocking compile diagnostics `120 -> 119`; the 77-statute gate
remains score-stable (`0 improved, 0 regressed`).

Schedule-list-entry replacements can replace one source-proven entry with
multiple source-visible sibling entries when the instruction says `for the entry
relating to X substitute` and the payload is a visible run of section-entry
sentences. Rule `uk_effect_schedule_list_entry_replace` now preserves
`replacement_texts`, permits explicit schedule partition carriers, and replay
slice-replaces only the uniquely matched direct `schedule_entry` child. Missing
or duplicate anchors still block; the rule is not a carrier-wide replacement.
Current witness: `ukpga/1990/8` affected `Sch. 16 Pt. 1` by `anaw/2015/4`
Sch. 4 para. 23(2), source text `For the entry relating to sections 61 and 62
substitute ...`. This cuts `ukpga/1990/8` blocking compile diagnostics
`119 -> 118`; the 77-statute broad gate remains score-stable (`0 improved,
0 regressed`).

Definition-child structural sibling insertions can also be owned by a single
source row, not only by a local parent/child source pair, when the row names the
exact section/subsection target, the definition term, the anchor child label, and
the next contiguous child label. Rule
`uk_effect_definition_child_structural_sibling_insert_lowered` emits a typed
`item` insert under the exact target path and records the source anchor child.
Current witness: `ukpga/1990/8` affected `s. 336(1)` by `ukpga/2008/29` s. 201,
source text `in the definition of "local authority" after paragraph (aa)
insert- ab ...`. Intercalated forms such as `after paragraph (e) insert- ea ...`
still block without an explicit tail-connector claim. The 77-statute broad gate
remains score-stable (`0 improved, 0 regressed`); the `ukpga/1990/8` aggregate
blocking count stays at 118 because a separate `e -> ea` intercalated row is now
classified under the explicit definition-child blocking family instead of the
generic structural-sibling family.

`lawvm uk-misses` now mirrors this distinction: JSON output retains
`rejection_rule_counts` for all compile diagnostics and adds
`blocking_rejection_rule_counts` for strict replay barriers; human output prints
separate "COMPILE DIAGNOSTICS" and "BLOCKING COMPILE REJECTIONS" sections. This
keeps statute-level miss triage aligned with broad-baseline workqueue semantics.

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

Remaining source-ledger candidates (each needs a verified failing case before
building — do not add guards for hypothetical bugs). These are not current
all-10 replay tasks; the 2026-05-31 all-10 pass moved the hard-set leftovers
into explicit manual/source/grounding frontiers unless noted otherwise:

1. `UK_RULE_UNCOMMENCED_EFFECT_RESOLVER` (§6.8) — the PIT-aware *resolver* on top of
   the sensor: decide application per the oracle version / `authority_mode` instead
   of silently applying. Verified mixed-sign as a blanket current-text gate, so it
   must be built only with a specific as-of/date witness and authority-mode
   contract.
2. `UK_RULE_REPEAL_OF_REPEAL_NO_REVIVE` (§6.1.13) — now has direct source-phrase
   witnesses in the all-archive scan and a bounded phrase-bearing-affecting-Act
   effect-candidate lane, but still needs a selected source/target corpus witness
   before replay changes. `UK_RULE_REPEAL_NO_DOUBLE_ENTRY` (§6.1.5) is now
   implemented for exact body+Schedule duplicate structural repeals with diagnostic
   rejection records.
3. **DONE (diagnostic surface)** SI semantics from
   `STATUTORY_INSTRUMENT_PRACTICE_5TH_ED`: `scripts/uk_si_semantics_scan.py`
   now records commencement metadata/body clauses, vires/enabling-power recitals,
   extent/application candidates, revocation/lapse candidates, correction-slip
   markers, and SI structure vocabulary. It is deliberately replay-neutral; the
   next step is adjudicating a concrete family before changing lowering/replay.
4. **PARTIAL (UK branch graph prototype + structured import)** Proposed-law
   authority paths from OPC/Cabinet sources: `lawvm uk-branch-demo` proves a
   UK-shaped proposed-law payload can be represented in the shared graph without
   leaking into enacted replay, and `lawvm uk-branch-import <payload.json>` now
   imports explicit owned proposed-law claims into that lane. Remaining work is
   real source acquisition/parsing for draft bills, carry-over, Parliament Act /
   Money Bill routes, financial-resolution provisional text, and Crown
   application.
5. The big EID divergence on the gate statute (ukpga/1978/30) is a Schedule-1
   **crossheading-representation** mismatch (`schedule-1-crossheading-…` +
   `_paragraph-wrapperNnM`), i.e. oracle editorial structure vs replay — saturated
   frontier, not a lowering gap.
6. The current largest broad-gate signal is **grounding/eId harmonization**, not a
   per-statute parser/replay family: the post-all-10 77-statute gate still carries
   `n_grounding_collateral=6170`, with `grounding_dominated_residual` rows such as
   `ukpga/1980/65`, `ukpga/1966/42`, and EU annex eId asymmetry. Fixes must make
   owned eId synthesis surfaces agree or keep collateral out of work selection; they
   must not delete replay state to match an oracle editorial projection.
7. Manual-frontier rows in the current hard-set have claim-template coverage after
   adding templates for `uk_manual_frontier_table_entry_placement_insert` and
   `uk_manual_frontier_savings_qualified_text_omission_candidate`. Those templates
   are still non-executable; they define validator obligations for future claims.

Each needs the standard ownership package (`AGENTS.md §7/§15`): stable rule id,
finding/observation, strict-mode behaviour, synthetic + corpus + negative tests.

# UK Official Drafting / Practice Source Ledger (Track G)

LawVM's UK rules need not be mined only by reverse-engineering effect feeds and
XML. The UK publishes an official drafting/practice layer that is a **Tier-1
authority** for "normal drafting intention" — the source-only side of the
correct-by-construction compiler (`AGENTS.md §2.1`). This ledger ranks that layer
and maps each mineable rule to a LawVM destination.

Authority tiers (use to rank a mined rule when it conflicts with observed
practice):

```
Tier 1: official drafting guidance (OPC / TNA / Cabinet Office / Parliament)
Tier 2: official explanatory or procedural manuals
Tier 3: observed effect-feed / XML practice
Tier 4: corpus-derived heuristics
```

A Tier-1 rule is authority for *what a faithful compiler should do*, not a
statute-level oracle: OPC's own preface says the guidance is not comprehensive and
drafters may depart from it. So it strengthens **source-faithfulness invariants and
diagnostics**; it does not become a new oracle to benchmaxx against.

---

## Sources

| source_id | authority | scope | primary LawVM use | acquired |
|---|---|---|---|---|
| `OPC_DRAFTING_GUIDANCE_2024` | OPC drafting guidance (Tier 1) | UK Government Bills / OPC practice | amendment grammar, repeal/substitution/insertion semantics, non-textual modification, uncommenced material | `.tmp/uk_guides/opc_drafting_guidance_2024.{html,txt}` (2024-03-19 ed.) |
| `OPC_COMMON_LEGISLATIVE_SOLUTIONS` | OPC + drafting offices (Tier 1) | policy→mechanism pattern catalogue | operation families / target-surface semantics; mostly MeVM-relevant | no |
| `GUIDE_TO_MAKING_LEGISLATION` | PBL Committee secretariat (Tier 2) | bill process; delegated powers; Henry VIII; commencement powers | delegated-power & commencement-power diagnostics | no |
| `STATUTORY_INSTRUMENT_PRACTICE` | The National Archives (Tier 1/2) | SI preparation, vires, laying, scrutiny | SI replay & vires constraints; SI source interpretation | no |
| `ERSKINE_MAY` | UK Parliament (Tier 2) | parliamentary procedure/convention | procedural validity, consent, carry-over | no |
| devolved manuals (SSI / Welsh SI / NI rules; SP/Senedd/NIA bill guidance) | devolved bodies (Tier 1) | ASP / Welsh / NI drafting | ASP/Welsh/NI frontends (follow-up) | no |

Acquisition follow-up: fetch CLS, GtML, SIP, and the devolved manuals before
claiming the UK source stack is complete.

---

## Mined rules → LawVM destinations (OPC Drafting Guidance Part 6)

Status legend: **HAVE** (implemented, verify), **GAP** (not handled / partial),
**SPEC** (diagnostic/spec-yield, no replay).

### §6.1 Repeals
- **6.1.2 operative form** `In the [Act] omit section 10 / omit Part 2`; whole-Act
  `The [Act] is repealed`. → UK repeal lowering. **HAVE (verify).**
- **6.1.3/6.1.5 repeal Schedule, no double-entry** — a repeal appears in the body
  *or* a repeal Schedule, never both (`Commissioner of Police v Simeon`). →
  `UK_RULE_REPEAL_NO_DOUBLE_ENTRY`: when the same target is repealed by both a body
  omit and a Schedule extent row, dedup to one op and emit an observation, do not
  apply twice. **GAP — propose.**
- **6.1.6/6.1.7 repeal of amendments only when completely superseded** → diagnostic
  for body-repeals of amending provisions. **SPEC.**
- **6.1.13 repeal of a repeal does not revive** (Interpretation Act 1978 s.15, subj.
  s.16 savings) → `UK_RULE_REPEAL_OF_REPEAL_NO_REVIVE`: a repeal op whose target is
  itself a repealing provision must not resurrect the originally-repealed text.
  **GAP — propose + negative test.**
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
  **GAP — this is the principled fix that retires the fuzzy-grounding crutch for
  inserted subtrees (see "Grounding" below).**

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
  modification lane. Replaying it as text is over-application. **GAP — propose +
  negative test; clean source-faithfulness win.**

---

## How this resolves the open matters (the optimal e2e route)

The session's stuck case (`ukpga/1998/17`) and the grounding `(a)/(b)` question both
resolve once the **correct-by-construction** rules above land — the EID-similarity
metric is a regression guard, not the objective (`§2.1`).

1. **Uncommenced / over-application (highest value).** `ukpga/1998/17`, the original
   Theft-Act case, and the `−0.56` "regression" from re-landing #52 are all entangled
   with applying effects the oracle correctly omits because they are uncommenced. Fix
   `UK_RULE_UNCOMMENCED_EFFECT_NOT_APPLIED` (§6.8) first: stop mutating the current
   consolidation with prospective effects. This is source-faithful and authority-backed,
   and removes a whole class of forbidden over-repeal.

2. **Grounding is a crutch, not a thing to tune.** `ground_ids()` renames replay eIds
   to match the oracle; for deep nodes with no structural address it *guesses* by
   fuzzy text, which manufactures the **spurious matches** that inflate the metric and
   move under unrelated edits (the #53 instability). Two ways to arbitrate that fuzzy
   contention — walk-order (a Python accident, `§1.7`) vs. global score — are *both*
   heuristics over an underdetermined population. **Direction (b)** removes the
   contention entirely: implement `UK_RULE_INSERTED_PROVISION_EID` (§6.4) so inserted
   subtrees carry the oracle-correct structural eId from synthesis and ground exactly.
   That is the optimal fix; fuzzy grounding then only covers genuinely
   structure-less residue, and a score-ranked stable assignment there is a strictly
   secondary cleanup.

3. **Non-textual modification gate (§6.9)** and **no-double-entry / no-revive (§6.1)**
   are independent source-faithfulness wins that each prevent a distinct over-mutation.

Verification for any of these uses the broad baseline, not the 9-statute gate:
`scripts/uk_broad_baseline.py --ids $(cat scripts/baselines/uk_grounding_corpus.txt)
--out .tmp/after.json` then `--compare scripts/baselines/uk_broad_2026-05-30.json
.tmp/after.json`. A score *drop* that corresponds to removing an over-applied or
spuriously-grounded match is a **correctness gain the guard mis-penalises** — confirm
at the EID level which matches moved, never trust the aggregate delta (`§2.1`).

---

## First implementation targets (ranked)

Cleanest deterministic wins first; the PIT-conditional one is harder and is a
frontier lane, not a one-line gate.

1. `UK_RULE_INSERTED_PROVISION_EID` (§6.4) — derive inserted-subtree structural eIds
   from the official numbering algorithm; retires the fuzzy-grounding crutch
   (direction b). Deterministic, correct-by-construction, no PIT dependence.
2. `UK_RULE_NON_TEXTUAL_MODIFICATION_NOT_TEXT_REPLAY` (§6.9) — gate "as if … there
   were substituted" / "applies … with modifications" out of textual replay.
3. `UK_RULE_REPEAL_NO_DOUBLE_ENTRY` (§6.1.5) and `UK_RULE_REPEAL_OF_REPEAL_NO_REVIVE`
   (§6.1.13).
4. `UK_RULE_UNCOMMENCED_EFFECT_OWNED_LANE` (§6.8) — own the prospective-only effects
   as a PIT-conditional lane (NOT a blanket gate; verified mixed-sign above).
   Larger temporal-model change; do after the deterministic wins, with the broad
   baseline as the guard.

Each needs the standard ownership package (`AGENTS.md §7/§15`): stable rule id,
finding/observation, strict-mode behaviour, synthetic + corpus + negative tests.

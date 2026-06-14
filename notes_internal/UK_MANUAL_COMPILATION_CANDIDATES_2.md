# UK Manual-Compilation Frontier Candidates — Pass 2

Status: analysis + proposal. Not normative. Does not change replay/compile code.
Author lane: `stream-uk-manual-compilation-candidates-2`.
Companion to `notes_internal/UK_MANUAL_COMPILATION_CANDIDATES.md` (pass 1, the 8
catalogued categories). This pass widens the statute sweep and surfaces **new
categories** the first pass did not name.

## Scope, method, discipline

The terminal UK product is a correct-by-construction consolidation (AGENTS
§2.1). A *manual-compilation frontier* candidate is a divergence/effect the
source does **not** deterministically specify the result of, **even in theory**:
no parse of the effect feed + affecting-act XML can supply the missing
determination. It needs an owned CLAIM that becomes authoritative input, not a
guessed op.

Strictness rules applied (candidates dropped if they fail any):

- **NOT acquisition gaps.** All `*_chain_gap` / `*_source_insufficient` rows are
  excluded — they are "fetch the missing intermediate source", deterministic
  once acquired. (These dominate the residual: in the sweep,
  `text_patch_target_source_chain_gap` 261, `text_patch_preimage_chain_gap` 239,
  `text_patch_postimage_chain_gap` 102, `source_pathology_insufficient` 108,
  `missing_payload_source_insufficient` 53 — all out of scope.)
- **NOT editorial crossheadings / facet-routing alone.** A facet target that is
  deterministically identifiable ("the italic heading immediately preceding
  s. 43") is a *facet-machinery* need, not non-determinism-in-theory. Such rows
  (`crossheading_candidate`, `heading_facet_candidate`,
  `table_crossheading_candidate`, `schedule_note_candidate`) are listed under
  "Adjacent but NOT counted" and excluded from the 20.
- **NOT already-harvestable deterministic rules.** Rows whose
  `manual_compile_status` is `deterministic_frontend_candidate` are deterministic
  once a frontend split/selector is written; only counted when the *selection*
  itself is a legal-applicability judgement (savings, scoped occurrence).

### Sweep

```
export LAWVM_CANONICAL_DATA_ROOT=<DATA_ROOT>
uv run lawvm uk-effects <id> --json --limit 2000        # all rows, read manual_compile_frontier.*
uv run lawvm uk-effects <id> --manual-compile-status <status> --json
uv run lawvm uk-effect   <id> <effect_id> --show-text
```

22 statutes compiled across deliberately different lanes (none repeated from
pass 1):

- **Scottish (asp):** asp/2010/8, asp/2003/13, asp/2009/9 (+ asp/2016/11 empty)
- **NI Assembly (nia):** nia/2015/9, nia/2002/14 (+ nia/2011/2 empty)
- **Welsh / Senedd (asc):** asc/2021/2, asc/2020/1
- **EU-retained (eur):** eur/2016/679 (UK GDPR)
- **SIs (uksi):** uksi/2002/3026, uksi/2017/469, uksi/2000/1043 (+ uksi/2012/1206
  empty)
- **ukpga, varied era / schedule-heavy:** 1996/18, 2000/8, 2005/5, 2006/46
  (Companies Act 2006), 2007/15, 2010/15, 2014/12, 2016/3

Actionable manual-frontier rule distribution across the sweep (frontier rows
only, acquisition gaps already removed):

```
manual_compile_candidate:
  105 table_entry_candidate            18 appropriate_place_definition_entry
   42 appropriate_place_candidate      15 heading_facet_candidate
   13 crossheading_candidate            8 repeal_table_candidate
    7 schedule_list_entry_candidate     3 source_carried_structured_text_patch
    2 whole_act_word_level_text_patch   2 deictic_structural_sibling_insert
    1 each: structural_sibling_insert, schedule_note, cross_container_renumber,
           appropriate_place_index_entry, amendment_program_target
source_or_feed_target_conflict:
    4 child_qualified_word_omission_target_mismatch
non_textual_or_out_of_scope:
 7448 non_textual_or_out_of_scope (mostly "coming into force"; see below)
   72 application_by_reference_out_of_scope
   39 unsupported_effect_family         2 empty_type_whole_act_action
```

The `non_textual_or_out_of_scope` mega-bucket is NOT uniform. Decomposing its
effect types (Companies Act 2006, 2000 rows): `coming into force` 1506,
**`applied` 346, `modified` 94, `excluded` 11, `restricted` 3, `disapplied` 3,
`transfer of functions` 3+1, `extended (Isle of Man) …` 2** — the non-commencement
remainder is where the new categories live.

---

# Part A — New instances of the 8 catalogued categories

These confirm the pass-1 families generalise beyond ukpga to devolved /
EU-retained / SI corpora and across eras.

### A1 — Appropriate-place index/list entry (cat. 2) — Tribunals act
`ukpga/2007/15` s. 8(2) ← `ukpga/2022/35`/SI program, effect
`key-003b5f455704861c9ecb1de75dbbc179`, *words inserted*. Source:
> "In section 8(2) … **at the appropriate places** insert— section 29B; section …"
Note the **plural** "appropriate places": one instruction, several editor-chosen
alphabetical slots. Non-deterministic in theory (placement delegated). Rule
`uk_manual_frontier_appropriate_place_index_entry_candidate` /
`appropriate_place_candidate`. **Fits existing** (`appropriate_place_mutation` →
`appropriate_place_anchor_or_ordering_claim`).

### A2 — Appropriate-place definition entry (cat. 1) — Welsh / Scottish corpus
18 instances in the sweep, e.g. `asp/2010/8` sch. 8 ← `uksi/2012/1659` Sch. 3
para. 26(3), `key-00297d247e58407725aba3dc83d716d5`:
> "In schedule 8 … **at the appropriate place** insert "British Waterways Board"."
Same alphabetical-slot non-determinism as pass-1 cat. 1, now witnessed in a
Scottish act amended by a UK SI. **Fits existing.**

### A3 — Amendment-program deictic target (cat. 4) — into EU-retained law
`eur/2016/679` (UK GDPR) Art. 6(3)/para. 5 ← `ukpga/2025/18` (Data (Use and
Access) Act 2025) s. 68(3), effect `key-066da07111bd6379774c51fb0b3db574`,
*inserted*:
> "After paragraph 5 **(inserted by section 67 of this Act)** insert— 6 …"
The anchor (para. 5) is created by a *sibling* provision (s. 67) of the same
affecting Act; the single row points at a target absent from the base. Identical
deixis to pass-1 cat. 4, now crossing a primary UK Act into retained EU
regulation text. Rule `uk_manual_frontier_amendment_program_target_candidate` (a
sibling `deictic_structural_sibling_insert_candidate` appears at Art. 6(3) ←
`ukpga/2025/18` s. 72(2)(b), `key-dd49e989c48018ae5e3b4a375f0189c4`). **Fits
existing** (`amendment_program_target_source_payload_and_boundary`).

### A4 — Span-vs-enumeration repeal table (cat. 3) — devolved/SI corpus
8 `repeal_table_candidate` rows in the sweep (e.g. asp/2010/8 sch. 19/20,
`no_unique_matching_repeal_table_row`). Same lossy range-vs-table-cell
non-determinism as pass-1 cat. 3. **Fits existing** (`repeal_table_candidate`),
same M3 caveat (no cross-act-placement proof for compound citations).

---

# Part B — NEW CATEGORIES (not in the pass-1 list of 8)

The first pass missed an entire axis: **non-textual / scope-modifying effects**.
These are the `applied / modified / excluded / restricted / extended / transfer
of functions / applied-by-reference` effect families. Each is non-deterministic
for textual consolidation in a way the 8 pass-1 categories never capture, and
none of them have a claim template or proof semantic today.

## NEW CATEGORY N1 — Extent-conditional modification ("extends to X **with modifications**")

**Concrete instance.** `ukpga/2006/46` (Companies Act 2006) Pt. 28 Ch. 1 ←
`uksi/2008/3122` art. 2 + Schedule, effect
`key-6405914e4020aea09f1078a3bb440625`, effect type **`extended (Isle of Man)
(with modifications)`**, effective 2009-03-01. Source witness (the affecting
Schedule):
> "SCHEDULE — Modifications with which Chapter 1 of Part 28 of the Companies Act
> 2006 extends to the Isle of Man … 1 In section 948 … a omit subsections (4)
> and (5); … c … for "the Data Protection Act 1998" substitute "the Data
> Protection Act 2002 (an Act of Tynwald: c 2)" …"

(11 paragraphs of omit/insert/substitute against ss. 948–964.)

**Why non-deterministic in theory.** This is not one consolidated text. The
effect creates a **territorially-scoped variant**: the GB text of Pt. 28 Ch. 1
is unchanged, but a *parallel* Isle-of-Man-extent version exists in which the
listed modifications apply. Which printed text is "the law" depends on the
**extent dimension** (where the provision is being read). A single linear
consolidation cannot represent both; choosing one silently drops the other.
Today replay even mis-lowered it to `repeal part:28/chapter:1 payload=subsection`
— an over-application (the forbidden direction, §2.1) — but the row is correctly
held at the frontier (`non_textual_or_out_of_scope`, no template).

**Missing fact / claim shape.** An **extent-scoped overlay**: (target region,
extent/territory key, the structured modification program, the base version it
forks from). The claim must produce a *variant* materialisation keyed on extent,
not mutate the base.

**Fits existing machinery: NO.** No `extent` / `applied_with_modifications`
action family, no proof semantic, not in `UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`. The
modification *program* inside the schedule is itself a normal amendment program —
so the inner ops could reuse existing families — but the **extent-keyed forking
of the materialised text** is a new core concern (PIT materialisation already
exists; an *extent* axis alongside the time axis does not). See **M4**.

## NEW CATEGORY N2 — Application / modification overlay ("applied … as modified by …")

**Concrete instances.** Across the sweep this is the largest non-commencement
out-of-scope family (`applied` 346 + `modified` 94 + `excluded`/`restricted`/
`disapplied`/`applied in part` ~20 in Companies Act 2006 alone):
- `ukpga/2006/46` s. 1297 ← `uksi/2007/1093` art. 10, `key-1c2430f71ad8df…`,
  **`modified`**.
- `ukpga/2006/46` ss. 182–186 ← `uksi/2008/432` art. 17(1) Sch. para. 2(f),
  `key-08f769a4400da32fe118090a09e8b15d`, **`modified (temp.)`**.
- `ukpga/2006/46` s. 232 ← `uksi/2008/432` art. 11(3), `key-cd0243bccee98559…`,
  **`excluded (temp.)`**.

**Why non-deterministic in theory.** "Provision P applies as modified by Q for
the purposes of R" does **not** rewrite P's printed text. It establishes that, in
a bounded **purpose/context scope**, a *different* reading of P governs. The
consolidated text of P is unchanged; the modified reading exists only within the
scope R. There is no single text the source determines — the result is a
context-indexed family of readings, and which applies is an applicability
judgement (which purpose, which class, often `(temp.)` so also time-bounded). The
classifier already names this honestly (`uk_non_textual_modification_out_of_scope`,
citing OPC Drafting Guidance Part 6.9) — it is *correctly* outside textual replay,
which is exactly why it is a manual-frontier claim, not a deterministic op.

**Missing fact / claim shape.** A **scoped application/modification overlay**:
(target, application-scope predicate = purpose/class/extent, optional temporal
window, the modification, the affecting instrument). The safe default is to leave
P's text intact and record the overlay as a typed non-replayable finding.

**Fits existing machinery: NO.** No application/modification action family or
proof semantic; not in the template set. This is the highest-*volume* new
frontier. See **M5**.

## NEW CATEGORY N3 — Transfer-of-functions placement

**Concrete instance.** `ukpga/2006/46` s. 1240 (and s. 1239, Sch. 11 para.
8(1)(a)) ← `uksi/2008/496` art. 3, effect **`transfer of functions`**,
`key-50ac0db854cd00d715765d5b77254d95`, effective 2008-03-01.

**Why non-deterministic in theory.** A transfer-of-functions order moves a
*function* from body A to body B. It does not, of itself, fix a single textual
edit: the printed reference may stay ("the Secretary of State" read as the new
body by operation of the order), or be re-pointed, and the determination of which
occurrences across the Act are affected — and whether the text is rewritten or
merely *read* differently — is a legal/editorial judgement the order delegates.
The feed records the effect against named provisions but does not enumerate the
exact textual consequence. Distinct from a plain `applied` overlay because it has
a **migration/identity** flavour (functions, not text), so it can also collide
with §1.6 lineage requirements if compiled as a rename.

**Missing fact / claim shape.** A **function-transfer determination**: (from-body,
to-body, affected provisions, and per-occurrence "re-point text" vs "read as"
disposition). Likely a typed non-replayable finding plus, where the order *does*
re-point text, owned text ops.

**Fits existing machinery: NO.** No transfer-of-functions family/proof/template.
See **M5** (same overlay machinery, distinct disposition).

## NEW CATEGORY N4 — Application-by-reference with embedded deixis ("applied by SI … (as inserted)")

**Concrete instance.** `asp/2003/13` s. 100 ← `ssi/2017/229` reg. 24(3), effect
type **`applied by SSI 2005/467 reg. 33(2) (as inserted)`**,
`key-14e2626aff8e6ff508708b7dd0325672` (72 such rows in the sweep, all
devolved). The effect *applies* the affected provision by reference to a target
that is itself **"(as inserted)"** by another instrument.

**Why non-deterministic in theory.** Two compounded non-determinisms: (a) it is
an application-by-reference (N2-class: no text edit, scoped reading), AND (b) the
referenced applying provision is identified deictically ("as inserted"), so even
locating the operative rule requires resolving an amendment program in a *third*
instrument. The textual consolidation of the affected Scottish act cannot be
determined from this row at all.

**Fits existing machinery: NO.** `application_by_reference_out_of_scope` is not in
the template set; it compounds N2 with cat-4 deixis. See **M5** + **M6**.

## NEW CATEGORY N5 — Source/feed target conflict (source scopes to a child the feed does not)

**Concrete instance.** `ukpga/2005/5` (ITTOIA 2005) s. 536(1) ← `ukpga/2020/14`
s. 37(3)(a)(5), `key-11d0fad0bbb64b416dabc92d02c9b9ab`, **`word omitted`**:
> "omit the "and" at the end of **sub-paragraph (i)**"
while the effect feed targets s. 536(1) (the parent). Also `ukpga/2007/15` Sch. 5
para. 22(1) ← `ukpga/2022/35`: "omit "and" at the end of **paragraph (b)**".
4 instances; status `source_or_feed_target_conflict`.

**Why non-deterministic in theory (bounded).** The source text and the official
feed name *different* targets for the same effect. Replay must not silently
prefer one (`§1.1` target hijacking); which surface is authoritative is a
reconciliation that — when the child named in the source is genuinely ambiguous
against the feed target — cannot be resolved by parsing either side alone.
*Honest caveat:* in the common "omit 'and' at the end of (i)" shape the child is
usually deterministically locatable, so many instances are
`deterministic_frontend_candidate` once a child-scoped omission selector exists.
It is counted here as a **conflict-class** candidate because the source-vs-feed
*disagreement* is the residual that needs an owned adjudication, not the omission
itself.

**Fits existing machinery: PARTIAL.** Rule *is* in the template set
(`child_qualified_word_omission_target_mismatch` →
`source_target_reconciliation` / `source_feed_target_reconciliation_claim`), but
the proof only records the reconciliation; it does not *decide* it. The decision
remains a non-replayable obligation, which is the correct honest state.

---

# Part C — Candidate ledger (the ~20)

| # | Category | Statute / provision | Affecting | Effect / rule | Why non-det. (1-line) | Machinery |
|---|----------|---------------------|-----------|---------------|------------------------|-----------|
| 1 | New N1 extent-with-modifications | ukpga/2006/46 Pt.28 Ch.1 | uksi/2008/3122 art.2 Sch. | `extended (IoM) (with modifications)` | creates a territorially-scoped *variant* text; no single consolidation | **NEW (M4)** |
| 2 | New N2 application overlay | ukpga/2006/46 s.1297 | uksi/2007/1093 art.10 | `modified` | context-scoped reading, P's text unchanged | **NEW (M5)** |
| 3 | New N2 application overlay (temp.) | ukpga/2006/46 ss.182–186 | uksi/2008/432 art.17(1) Sch. para.2(f) | `modified (temp.)` | scoped + time-bounded reading | **NEW (M5)** |
| 4 | New N2 exclusion overlay (temp.) | ukpga/2006/46 s.232 | uksi/2008/432 art.11(3) | `excluded (temp.)` | provision disapplied for a scope/window | **NEW (M5)** |
| 5 | New N2 restriction overlay | ukpga/2006/46 s.754 | uksi/2008/346 Sch. para.6 | `restricted` | bounded-scope restriction, no text edit | **NEW (M5)** |
| 6 | New N3 transfer of functions | ukpga/2006/46 s.1240 | uksi/2008/496 art.3 | `transfer of functions` | function moves, textual consequence delegated | **NEW (M5/M6)** |
| 7 | New N4 application-by-ref + deixis | asp/2003/13 s.100 | ssi/2017/229 reg.24(3) | `applied by SSI 2005/467 (as inserted)` | scoped reading via a deictically-located rule | **NEW (M5+M6)** |
| 8 | New N4 application-by-ref + deixis | asp/2003/13 s.250(7) | ssi/2017/232 reg.8 | `applied by SSI 2008/356 reg.8A(4) (as inserted)` | as #7 | **NEW (M5+M6)** |
| 9 | New N5 source/feed target conflict | ukpga/2005/5 s.536(1) | ukpga/2020/14 s.37(3)(a)(5) | `word omitted`, source says sub-para (i) | source and feed name different targets | PARTIAL |
| 10 | New N5 source/feed target conflict | ukpga/2007/15 Sch.5 para.22(1) | ukpga/2022/35 Sch.5 para.29(3)(a) | `word omitted`, source says para (b) | as #9 | PARTIAL |
| 11 | Cat.1 appropriate-place definition | asp/2010/8 sch.8 | uksi/2012/1659 Sch.3 para.26(3) | `words inserted`, "at the appropriate place" | alphabetical slot delegated to editor | fits existing |
| 12 | Cat.2 appropriate-place index (plural) | ukpga/2007/15 s.8(2) | (program) | `words inserted`, "at the appropriate **places**" | several editor-chosen slots, one instruction | fits existing |
| 13 | Cat.4 amendment-program deixis (EU-retained) | eur/2016/679 Art.6(3) para.5 | ukpga/2025/18 s.68(3) | `inserted`, "(inserted by section 67 of this Act)" | anchor created by sibling provision; absent in base | fits existing |
| 14 | Cat.4 deictic structural sibling insert | eur/2016/679 Art.6(3) | ukpga/2025/18 s.72(2)(b) | `words inserted`, "after that subparagraph" | anchor identity depends on program order | fits existing |
| 15 | Cat.3 span-vs-enum repeal table | asp/2010/8 sch.19 | asp/2012/8 sch.7 para.40(6) | repeal-table, `no_unique_matching_repeal_table_row` | feed range vs enumerated table cell | fits existing (M3) |
| 16 | Cat.8-adjacent structural sibling insert in definition | ukpga/2006/46 s.474(1) | uksi/2009/1342 art.26 | `words inserted` after para (c)/(g) in a definition | two anchored child inserts in one definition; boundary delegated | fits existing |
| 17 | Cat.5-class savings-qualified omission (whole-Act, with exclusions) | ukpga/2005/5 (Act) | ukpga/2005/11 Sch.4 para.132(1) | `words substituted` "wherever it appears (except as provided by para 133(2)(b),(5))" | document-wide patch minus an exception set = applicability scope | fits existing (whole_act + savings) |
| 18 | Schedule-list-entry by anchor text | asp/2010/8 s.115(6) | asp/2012/8 sch.7 para.40(3) | `words substituted` "for the entry beginning "Her Majesty's Chief Inspector…"" | entry has no eId; identified by anchor text in collapsed schedule | fits existing (`schedule_list_entry_mutation`) |
| 19 | Cross-container renumber | asp/2003/13 s.273(1) | asp/2015/9 s.18(2)(a) | "words up to … become subsection (1)" | renumber/migration needs lineage map source omits | fits existing (`cross_container_renumber_migration`) |
| 20 | Source-carried structured text patch (carried child structure) | ukpga/2005/5 s.640(6)(b) | ukpga/2009/10 Sch.2 para.20(b) | `words inserted`, carried list children | feed target broader than carried child units; flattening loses structure | fits existing |

Extra (over 20, same families, additional witnesses):
- N2 disapplied: ukpga/2006/46 `disapplied` rows (3) — same as #4 class.
- N1 second extent witness: ukpga/2006/46 Sch.2 ← uksi/2009/1378 art.2,
  `extended (Isle of Man)` (no modifications — the *pure* extent case; still N1).

## Adjacent but deliberately NOT counted (dropped as not non-det.-in-theory)

- **Facet routing** (`heading_facet_candidate` 15, `crossheading_candidate` 13,
  `table_crossheading_candidate`, `schedule_note_candidate`): targets are
  deterministically identifiable ("heading to that section", "italic heading
  immediately preceding"); these need *facet machinery*, not a non-determinism
  claim. Already templated.
- **table_entry_candidate** (105): most sweep hits are whole-regulation /
  whole-schedule substitutions mis-bucketed via `overlap_substitution`, i.e.
  deterministic structural replaces, not genuine table-cell non-determinism.
- **`coming into force`** (1506): commencement metadata; matters to temporal
  selection (pass-1 cat. 6 family) but only the *conditional/contingent* subset
  is non-deterministic — counted there, not re-counted here.
- All `*_chain_gap` / `*_source_insufficient`: acquisition gaps, excluded.

---

# Summary table

| Category | Kind | # candidates | Fits-existing | Needs-new |
|----------|------|-------------:|--------------:|----------:|
| N1 extent-with-modifications | NEW | 2 (#1, extra) | 0 | 2 |
| N2 application/modification/exclusion/restriction overlay | NEW | 4 (#2–#5) | 0 | 4 |
| N3 transfer of functions | NEW | 1 (#6) | 0 | 1 |
| N4 application-by-reference + deixis | NEW | 2 (#7–#8) | 0 | 2 |
| N5 source/feed target conflict | NEW | 2 (#9–#10) | 0 (partial) | 2 (partial) |
| Cat.1/2 appropriate-place (new witnesses) | known | 2 (#11–#12) | 2 | 0 |
| Cat.4 amendment-program deixis (new corpus) | known | 2 (#13–#14) | 2 | 0 |
| Cat.3 span-vs-enum repeal table (new corpus) | known | 1 (#15) | 1 | 0 |
| Cat.8/structural/definition/list/renumber/whole-act | known | 5 (#16–#20) | 5 | 0 |
| **Total** | | **21 (+2 extra)** | **12** | **9 (incl. 2 partial)** |

**New categories discovered: 5** — N1 extent-conditional modification, N2
application/modification overlay, N3 transfer-of-functions, N4
application-by-reference-with-deixis, N5 source/feed target conflict. All five
sit in the `non_textual_or_out_of_scope` / `source_or_feed_target_conflict`
lanes that pass 1 did not mine. **9 candidates need new machinery** (N1–N4 = 7
fully new; N5 = 2 partial).

## Machinery changes (continuing pass-1's M1–M3 numbering)

- **M4 — Extent-scoped variant materialisation (N1).** The deepest new need: an
  *extent* axis alongside the existing time axis in PIT materialisation. The
  inner modification program reuses existing amendment families, but the result
  is a territory-keyed *fork* of the text. Add an `applied_with_modifications` /
  `extent_modification_overlay` action family whose claim owns (target region,
  extent key, base-version reference, modification program) and a validator that
  checks the program against the base without collapsing the GB text. Core-level:
  this is genuinely new (`§13` identity/lineage touches it).
- **M5 — Scoped application/modification overlay claim (N2, N3, part of N4).**
  Highest-*volume* gap. Add the out-of-scope effect families
  (`uk_manual_frontier_non_textual_or_out_of_scope`,
  `uk_non_textual_modification_out_of_scope`,
  `application_by_reference_out_of_scope`) to a *non-replayable-finding* claim
  kind: the claim owns (target, application-scope predicate, optional temporal
  window, modification, instrument) and the safe default is leaving the base text
  intact while recording a typed overlay finding. This is the OPC-Guidance-6.9
  family the classifier already names; the claim form is the missing half.
- **M6 — Deixis resolution inside application effects (N4).** Reuse the cat-4
  `amendment_program_target_source_payload_and_boundary` proof to resolve the
  "(as inserted)" reference, but applied to the *applying* instrument rather than
  the affected text — i.e. compose M5's overlay scope with cat-4 deixis. No new
  proof primitive, but a new composition.
- (M1–M3 from pass 1 still stand: contingent commencement, same-moment cross-act
  conflict, cross-act placement for compound references.)

All proposals preserve `notes/MANUAL_COMPILATION_CLAIMS.md` discipline: the claim
proposes meaning; a deterministic validator checks it against source witnesses +
live target state; replay executes only validated claims; and the safe default
for N1–N4 is **non-replayable finding** (under-application), never silent
text mutation (the forbidden over-application direction, §2.1).

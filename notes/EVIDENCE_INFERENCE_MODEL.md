# Evidence Inference Model

The core idea is:

A section should become `PROVED_*` only when a named rule discharges a specific proof obligation from section-local evidence. Everything else is either a defeater or a typed unresolved sink.

That keeps proof discipline and still reduces `UNRESOLVED`.

Two current rules should be tightened before adding anything else.

First, “empty oracle text” is **not** the same as `contentAbsent`. That should only fire from explicit oracle metadata, not from emptiness alone.

Second, the new “no timeline entry” negative proof needs **exact canonical address matching**, not string containment on serialized addresses.

## The missing architecture

Right now `_build_section_claims()` is effectively:

generate candidates → append in ad hoc order → pick `candidates[0]`.

That is not a proof calculus. It is append-order selection.

Replace it with four explicit products:

`SectionEvidenceContext`
: all section-local facts, already joined to exact canonical address and affecting-act lineage.

`PositiveClaim`
: a proved claim with `rule_id`, `tier`, `kind`, `proof_shape`, `premises`, `support`, `defeaters_checked`.

`Defeater`
: a fact that blocks certain proof families without proving an alternative cause.

`UnresolvedSink`
: a typed unresolved category when no positive proof survives defeaters.

Then evaluate in this order:

1. Build section-local context.
2. Generate all positive claims whose premises hold.
3. Generate all defeaters.
4. Discard positive claims blocked by applicable defeaters.
5. Select the primary claim by an explicit precedence order, not append order.
6. If no positive claim survives, select the most specific unresolved sink.

The selection order should be declared once, for example:

`NONCOMMENSURABLE > SOURCE_PATHOLOGY > ORACLE_INCORRECT > REPLAY_BUG > UNRESOLVED`

and then within a tier:

`section-local exact proof > section-local comparative proof > statute-level support`

This enforces the publication goal: never count a Finlex error when the stronger proof is actually source pathology or non-commensurability.

## Which proof shapes are underused

The most underused proof shapes in the current classifier are these.

`2. Preservation certificate`
: `peg-audit` and `lower-audit` exist, but the classifier barely consumes them. That leaves many “payload prefers replay” cases stuck below their true strength.

`3. Ambiguity resolution witness`
: Elaboration observations, slot bindings, and leftovers are recorded. What is missing is a proof of whether there was exactly one admissible elaboration or several. Without that, “frontend ambiguity” is too sticky.

`4. Safety / invariant proof`
: Strict/runtime integrity signals are mostly used as mixed-risk hints, not as section-local proofs or defeaters.

`5. Temporal selection proof`
: You already have strong temporal logic in timelines, but oracle temporal impossibility is only partly used, mostly at statute level.

`8. Strictness proof`
: `strict_fail_reasons` are currently used too coarsely. The classifier needs section-local lineage strictness, not statute-global strictness.

`10. Non-commensurability proof`
: Cross-chapter drift, range folding, contingent commencement, temporary editorial retention all want to be explicit non-commensurability proofs rather than generic unresolved.

## High-value rules to add

### 1. `ORACLE.CONTENT_ABSENT_EXPLICIT`

Logical deduction:
If the oracle explicitly declares the consolidated text absent, and replay produces governing text for the section, then the oracle absence is established. This is oracle-side, not replay-side.

Consumes:
`contentAbsent` / absent-ajantasa metadata, replay PIT, exact section address.

Tier:
`PROVED_ORACLE_INCORRECT`

Fires when:
The oracle metadata explicitly says the relevant text is absent or unavailable. Not merely when `oracle_text == ""`.

False-positive risk:
Low, if driven by explicit metadata rather than emptiness.

Auditor check:
Open the oracle metadata and the replay PIT for that exact section.

This should replace the current empty-text shortcut.

### 2. `ORACLE.BASELINE_SOURCE_WITNESS_MATCHES_REPLAY`

Logical deduction:
If the divergence already exists before the blamed amendment, and the earliest authoritative source witness for the section matches replay while the oracle differs, then the divergence is preexisting oracle drift, not a replay bug.

Consumes:
Base statute text or earliest affecting amendment payload, section bisect baseline state, affecting acts, no-touch lineage after that witness.

Tier:
`PROVED_ORACLE_INCORRECT`

Fires when:
The source witness matches replay exactly or under a strict normalization, and no later touching act before the oracle cutoff changes the section.

False-positive risk:
Low when exact witness support is required and no source incompleteness exists on the witness chain.

Auditor check:
Compare the source witness, replay section, oracle section, and the affecting-act list.

This is the cleanest way to shrink `preexisting_baseline_residue`.

### 3. `ORACLE.STRICT_SOURCE_PAYLOAD_SUPPORTS_REPLAY`

Logical deduction:
If the blamed amendment’s payload is section-local, parses and lowers under a strict-clean path, and that payload supports replay more strongly than the oracle, the divergence is oracle-side, not merely “source pathology”.

Consumes:
Current `payload_vs_replay` / `payload_vs_oracle`, plus parse certificate, lowering certificate, section-local strict lineage, and absence of source pathologies for that source section.

Tier:
`PROVED_ORACLE_INCORRECT`

Fires when:
The payload support exists and all prerequisite certificates are section-clean.

False-positive risk:
Medium if partial fragments are allowed. Low if strict-clean payload extraction and exact target witness are required.

Auditor check:
Inspect the amendment payload, the parse/lower certificates, and the two similarity comparisons.

This should split the current `blamed_source_payload_prefers_replay` into:

* a stronger oracle-proof branch, and
* a weaker source-pathology branch.

### 4. `ORACLE.TEMPORAL_IMPOSSIBILITY_AT_CUTOFF`

Logical deduction:
If the oracle is effectively presenting a version that is temporally ineligible at the comparison date, then the oracle is wrong.

Consumes:
Oracle version-mid / cutoff metadata, timeline version dates, `effective`, `enacted`, `expires`, and query mode.

Tier:
`PROVED_ORACLE_INCORRECT`

Fires when:
The oracle-implied version is not legally selectable at the oracle cutoff or PIT date.

False-positive risk:
Low when the oracle version mapping is explicit.

Auditor check:
Check the oracle cutoff/version metadata and the timeline eligibility conditions.

This uses temporal selection proof much more directly.

### 5. `NONCOMM.ADDRESS_RELOCATION_EXACT`

Logical deduction:
If the replay section text matches another oracle section nearly exactly, and the disagreement is only in address projection within a preserved chapter/statute text bag, the problem is structural non-commensurability or oracle topology drift, not unresolved blame.

Consumes:
Alternative oracle/replay matches, chapter-local or statute-local text-bag comparison, lineage check for absence of move/renumber/relabel evidence.

Tier:
Usually `PROVED_HTML_XML_NONCOMMENSURABLE`; in cleaner cases `PROVED_ORACLE_INCORRECT` structural drift.

Fires when:
The relocation is exact enough and no legal move/renumber evidence explains it.

False-positive risk:
Medium unless lineage is checked for relabel/move. With that guard, low.

Auditor check:
Compare the two matched section texts and verify the absence of move/renumber lineage.

Use this rule for:

* `same_chapter_replay_section_drift`
* `cross_chapter_oracle_section_drift`

without guessing.

### 6. `ELAB.SINGLE_ADMISSIBLE_BINDING`

Logical deduction:
If elaboration required slot binding but the elaborator can prove there was exactly one admissible binding and zero leftovers, then “frontend elaboration ambiguity” is discharged.

Consumes:
A new elaboration certificate:

* candidate binding count
* chosen binding
* rejected binding count
* leftover count
* exact affected slots

Tier:
This is primarily a defeater-removal rule. It removes unresolved elaboration ambiguity. Combined with other evidence, it can unlock `PROVED_ORACLE_INCORRECT` or `PROVED_REPLAY_BUG`.

Fires when:
`candidate_count == 1` and `leftovers == 0`.

False-positive risk:
Depends entirely on whether the elaborator truly enumerates admissible bindings. If it does, low.

Auditor check:
Inspect the elaboration certificate and the slot-binding trace.

This is the biggest missing use of ambiguity-resolution proof.

### 7. `REPLAY.SECTION_LOCAL_INVARIANT_BREACH`

Logical deduction:
If a section divergence intersects a hard replay/runtime invariant violation in the same subtree, that is direct replay-side evidence.

Consumes:
`verify` / `check_invariants` / apply mutation paths, joined to section address.

Tier:
`PROVED_REPLAY_BUG`

Fires when:
A hard invariant breach is section-local to the diverging subtree.

False-positive risk:
Low only if the invariant itself is a hard semantic/runtime invariant, not a lint rule.

Auditor check:
Rerun verify, inspect the violating path, and show that it intersects the section.

This is where safety proof should become a positive replay proof, not just a mixed-risk badge.

### 8. `STRICT.SECTION_LOCAL_CLEAN_LINEAGE`

Logical deduction:
If every act affecting the section is strict-clean for that section, statute-level extraction fallback elsewhere cannot be used as a defeater here.

Consumes:
Per-amendment, section-local strict verdicts; affecting-act lineage.

Tier:
Not a direct proof tier. This is a defeater-removal rule.

Fires when:
All affecting acts for the section are section-clean.

False-positive risk:
Low if lineage is exact.

Auditor check:
List affecting acts and the strict verdict for each.

This is how `strict_fail_reasons` should mostly enter the classifier: section-locally.

### 9. `SOURCE.SECTION_SCOPED_CORRIGENDUM`

Logical deduction:
If an official or verified manual corrigendum directly patches the exact source-act section relevant to the divergence, the divergence is explained by source correction, not generic unresolved drift.

Consumes:
Section-scoped corrigendum support, source-act mapping, section lineage.

Tier:
Usually `PROVED_SOURCE_PATHOLOGY`. In a narrower class, if the oracle failed to absorb a corrigendum already incorporated by replay, it can support `PROVED_ORACLE_INCORRECT`.

Fires when:
The corrigendum is exact to the blamed source and section.

False-positive risk:
Low if the corrigendum-to-section mapping is exact.

Auditor check:
Open the corrigendum record and the affected source/oracle section.

### 10. `NONCOMM.TEMPORAL_CONTINGENT_SECTION_LOCAL`

Logical deduction:
If the divergence depends on contingent commencement / contingent effective date for the exact section lineage, then the comparison is temporally non-commensurable.

Consumes:
Contingent effective-date sources, section lineage, temporal selection witness.

Tier:
`PROVED_HTML_XML_NONCOMMENSURABLE` or a dedicated `PROVED_TEMPORAL_NONCOMMENSURABLE`

Fires when:
The exact section lineage crosses contingent temporal semantics and no stronger proof exists.

False-positive risk:
Low if the contingent source is explicit.

Auditor check:
Inspect the commencement source and the section lineage.

This is especially important if temporary overlays and contingent dates continue to matter.

## How the common unresolved kinds get resolved

`preexisting_baseline_residue`
: Use baseline source witness, exact no-touch lineage, and temporal impossibility. If those fail, keep unresolved.

`blamed_frontend_elaboration_ambiguity`
: Resolve with a single-admissible-binding certificate. Without it, keep unresolved.

`preexisting_frontend_elaboration_ambiguity`
: Same idea, but on the first-drop amendment rather than the blamed amendment.

`blamed_amendment_improves_section`
: This is mostly a defeater against blame. It often wants to combine with baseline witness proof or payload support. On its own it should usually remain unresolved.

`same_chapter_replay_section_drift`
: Promote when exact relocation / text-bag equality and absence of renumber/move lineage are proved.

`cross_chapter_oracle_section_drift`
: Same, but with stronger address relocation proof.

## New data the classifier should consume

The highest-value new inputs are these.

A `section-local strict lineage map`
: For each diverging section, list affecting acts and whether each is strict-clean, extraction-clean, lowering-clean, and invariant-clean.

A `parse certificate`
: For the blamed section and source act, prove that the relevant op(s) were extracted from a specific span under a specific rule, with no extraction fallback on that section.

A `lowering certificate`
: Prove that ParsedOp/ClauseAST lowering preserved the section-level effect family and target.

An `elaboration certificate`
: Record admissible slot-binding count, chosen binding, leftovers, and rejected bindings.

A `temporal selection witness`
: For each selected replay version and each oracle-implied version, expose why it is or is not eligible at the date.

An `oracle metadata witness`
: Explicit `contentAbsent`, version-mid, cutoff, and topology metadata, not inferred emptiness.

A `chapter/statute text-bag map`
: To prove structural relocation/non-commensurability without heuristics.

A `section-scoped corrigendum index`
: Corrigenda need to be consumable at section level, not only statute level.

## What should remain unresolved after all that

`UNRESOLVED` should not stay a single bucket. Split it into four families.

`UNRESOLVED.source_underdetermined`
: extraction coverage gap, context-dependent anchor resolution, unresolved elaboration choices.

`UNRESOLVED.preexisting_divergence`
: baseline residue, preexisting same-section structure drift, preexisting same-chapter drift.

`UNRESOLVED.address_projection`
: same-chapter or cross-chapter drift where the relocation proof is not yet exact enough.

`UNRESOLVED.temporal`
: contingent commencement, temporary editorial retention, temporal comparison layer mismatch.

That makes the queue actionable. “Unresolved” becomes a typed proof gap, not a dump bucket.

## How strict fail reasons should relate to tiers

`strict_fail_reasons` should have three roles only.

First, some are **defeaters**:

* parse extraction fallback
* lowering ambiguity
* context-dependent anchor resolution

These block claims that require complete, exact compilation.

Second, some are **direct positive evidence** if section-local:

* apply/runtime/tree invariant violations
* timeline invariant violations

These can support `PROVED_REPLAY_BUG`, but only if tied to the exact section subtree.

Third, some are **routing signals**:

* contingent effective date
* temporary overlay/editorial retention
* scope-dependent applicability

These do not prove oracle or replay fault. They route the section into non-commensurability or temporal unresolved.

The key rule is:

Do not use statute-global strict fail reasons directly at section level unless the section lineage intersects them.

## Publication-safety guarantees the classifier should enforce

For every published `PROVED_ORACLE_INCORRECT` section, these invariants should hold.

The claim has a named `rule_id`.
The rule’s premises are section-local or explicitly statute-level.
All defeaters required by that rule were checked and recorded.
No claim relies on “empty text implies contentAbsent”.
No claim relies on statute-global extraction failure unless section-localized.
The replay side is admissible enough for that rule:

* no section-local hard invariant breach unless the rule explicitly tolerates it
* no unresolved extraction gap in the affecting-act lineage unless the rule explicitly tolerates it
  Primary selection is by explicit precedence, never append order.
  Section-level counts and statute-level claims are kept separate in publication.
  Every published claim carries an audit record that an external reviewer can replay.

Add one more publication invariant:

A statute headline must be derived from section primary claims plus a small set of independent statute proofs like oracle cutoff drift. It should not separately reclassify from raw diagnoses.

That keeps statute rollup from drifting away from section truth.

## The practical implementation path

1. Replace append-order candidate selection with a rule registry and explicit precedence.
2. Tighten the two unsafe rules now:

   * empty text ≠ contentAbsent
   * string containment ≠ exact timeline absence
3. Add section-local strict lineage joins.
4. Add the safest high-yield proofs first:

   * explicit contentAbsent
   * baseline source witness
   * strict source payload support
   * temporal impossibility
5. Add exact relocation / chapter-bag non-commensurability.
6. Add elaboration certificates so frontend ambiguity can actually be discharged.
7. Split unresolved into typed sinks and publish that taxonomy.

The biggest likely wins are:

* `baseline_source_witness_matches_replay`
* `strict_source_payload_supports_replay`
* `address_relocation_exact`
* `section-local strict lineage`
* `single_admissible_binding`

Those are the places where the pipeline already has most of the raw material, but the classifier is not yet cashing it out into proof.

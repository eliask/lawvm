# Theory of LawVM

Status: draft.
Purpose: define what LawVM is, what it compiles, what its stable waists are, what kinds of proofs it can carry, and what the trusted kernels should be in a high-assurance architecture.

---

## 1. Thesis

**LawVM is a proof-disciplined compiler for legal state transitions.**

It compiles hostile legislative delta sources into a **temporal, queryable model of legal text-state**, together with explicit evidence for how each state transition was derived.

The most compact statement is:

> **LawVM = compiler from hostile legislative deltas to a proof-carrying temporal legal-state machine.**

LawVM is not primarily:

- a best-effort parser,
- a text diff tool,
- an oracle comparator,
- or a general legal reasoning engine.

Those may exist around it. The core job is narrower and sharper:

1. recover the legal text-layer state transitions encoded by amendment artifacts,
2. normalize them into a canonical temporal substrate,
3. materialize point-in-time law from that substrate,
4. and emit evidence explaining why that result should be trusted.

---

## 2. The domain

### 2.1 What the source language is

The source language is **legislative delta language**.

A source artifact may be:

- amendment prose,
- structured amendment metadata,
- effects feeds,
- commencement provisions,
- editorially imperfect XML,
- partial or stale corpus snapshots,
- or combinations of these.

Unlike ordinary programming languages, the source language is often:

- underspecified,
- context-dependent,
- non-local in meaning,
- historically messy,
- and contaminated by editorial or publication artifacts.

### 2.2 What the target is

The target is not machine behavior in the ordinary compiler sense.

The target is a **canonical temporal representation of legal text-state**:

- typed legal operations,
- provision timelines or an equivalent temporal graph,
- deterministic PIT materialization,
- lineage of changes,
- and proof artifacts explaining each transform.

### 2.3 What layer LawVM computes

LawVM computes the **text layer** of law.

It answers questions like:

- What did section X say on date T?
- Which act inserted or replaced this subsection?
- What is the active wording after commencement, expiry, and overlay selection?
- Which legal operations produced this current state?

LawVM does **not** by itself compute the full normative layer:

- purposive interpretation,
- doctrinal consequences,
- obligation and compliance reasoning,
- policy balancing,
- or judicially creative interpretation.

Those may be built on top of LawVM's output. They are not part of its core contract.

---

## 3. What kind of thing LawVM is

LawVM is simultaneously:

- a **compiler**, because it performs staged lowering from source artifacts to canonical execution artifacts,
- a **temporal database builder**, because the compiled product is time-indexed legal state,
- a **lineage engine**, because identities and transitions must be tracked across amendments,
- and a **proof-carrying checker**, because the system must explain why it believes the recovered meaning.

If one label must dominate, it should be:

> **LawVM is a temporal delta compiler with explicit epistemic accounting.**

---

## 4. Why LawVM is like a compiler, and why it is not

### 4.1 Similarities to ordinary compilers

LawVM has familiar compiler structure:

- tokenization / lexical classification,
- parsing,
- multiple IRs,
- lowering across waists,
- deterministic execution kernels,
- conformance tests,
- and invariant checking.

It also benefits from ordinary compiler discipline:

- stable intermediate representations,
- explicit ownership of semantics,
- boring back ends,
- separation of parse, elaboration, lowering, and execution,
- and tests that pin intermediate contracts, not only final output.

### 4.2 Differences from ordinary compilers

LawVM differs in several deep ways.

First, the source is a **delta against evolving state**, not a standalone program.

Second, meaning is often **live-state-dependent**. A clause may only become unambiguous when interpreted against the current statute structure.

Third, the source corpus is often **incomplete or editorially distorted**.

Fourth, the “oracle” is not always authoritative. Consolidated legal text may be stale, editorially annotated, or otherwise non-commensurable with the true PIT state.

Fifth, LawVM must compute not only semantic artifacts but also an **epistemic trail** explaining confidence, uncertainty, recovery, and blame.

So compared with a normal compiler:

- the **front end is harder**, because source recovery is harder,
- the **execution kernel is smaller**, because the operation algebra is small,
- and the **proof surface is broader**, because uncertainty and source pathology must remain explicit.

---

## 5. The machine model

A normal compiler ends in machine code.

LawVM has three analogous “low” levels:

1. **Canonical legal operations / intents**
   - replace, insert, repeal, relabel, text patch, effect intent, etc.

2. **Provision timelines / temporal graph**
   - the canonical executable artifact for legal text-state across time.

3. **PIT materialization**
   - the result of evaluating the temporal artifact at a date T.

The useful analogy is:

- clause surface ≈ high-level syntax tree,
- canonical operations ≈ low-level IR / assembly,
- timelines / graph ≈ linked executable artifact,
- PIT materialization ≈ program execution result,
- query engine ≈ machine.

The “CPU” is the legal-state evaluator:

- given a date,
- apply temporal selection,
- apply overlays,
- produce the active statute state.

---

## 6. The two-plane model

LawVM has two interleaved planes.

### 6.1 Semantic plane

This computes what the law’s text-state is.

Typical progression:

```text
SourceBundle
→ ClauseSurface
→ PayloadSurface
→ ElaboratedIntent
→ CanonicalOps
→ ProvisionTimelines / TemporalGraph
→ PIT Materialization
```

### 6.2 Epistemic plane

This computes why the system believes that semantic result.

Typical progression:

```text
ParseWitnesses
→ Observations
→ Obligations
→ Adjudications / Pathologies
→ Claims
→ EvidenceBundle
```

Ordinary compilers mostly live on the semantic plane.

LawVM must live on both.

That is not optional. If the epistemic plane is weak, the system may still produce text, but it cannot justify that text under high-assurance conditions.

---

## 7. The hard waists

LawVM should preserve a small number of stable waists.

### 7.1 Clause surface waist

The clause surface captures the amendment instruction language as a typed syntactic product.

Its job is to preserve:

- verb grouping,
- target structure,
- local syntactic distinctions,
- special targets such as heading or intro,
- and clause-local provenance.

It must not prematurely flatten away distinctions that later matter.

### 7.2 Payload surface waist

The payload surface captures the amendment body after source-local normalization, but before live-state-dependent elaboration.

Its job is to preserve:

- omission markers,
- sparse payload shape,
- wrappers,
- malformedness,
- and body-local structure.

It is the boundary between:

- source-local transforms, and
- live-dependent meaning recovery.

### 7.3 Canonical operation waist

Canonical operations are the replay execution contract.

Their job is to make explicit:

- action family,
- target family,
- payload,
- execution contract,
- occupancy assumptions,
- ordering semantics,
- and source provenance.

Below this waist, apply should not rediscover meaning. It should execute a contract.

### 7.4 Temporal graph waist

In the long run, a fourth practical waist should also be treated as first-class:

- provision timelines / temporal graph.

This is the true runtime substrate for PIT queries and lineage.

---

## 8. The refinement chain

The core theory of LawVM is a sequence of refinements.

Different arrows perform different jobs.

### 8.1 Observational transforms

These preserve source facts without deciding more than necessary.

Examples:

- tokenization,
- scanner tagging,
- clause surface extraction,
- payload surface extraction.

Rule:

> **Tag, don’t delete.**

Anything context-sensitive should survive long enough to be interpreted by a later layer.

### 8.2 Interpretive transforms

These recover meaning that depends on typed live snapshots.

Examples:

- omission alignment,
- sparse slot assignment,
- anchor resolution,
- broad-to-narrow reinterpretation,
- table-row grounding,
- VÄLIAIKAINEN overlay interpretation.

Rule:

> **Interpretation may depend on live state, but only through explicit bounded snapshots.**

### 8.3 Contract-lowering transforms

These turn interpreted meaning into executable contracts.

Examples:

- CanonicalIntent,
- LegalOperation,
- typed target lowering,
- occupancy and insertion contract lowering.

Rule:

> **Lowering must make implicit execution assumptions explicit.**

### 8.4 Operational transforms

These execute the contract into temporal state.

Examples:

- apply,
- timeline compilation,
- overlay selection,
- PIT materialization.

Rule:

> **Operational kernels should be small, deterministic, and semantics-poor.**

### 8.5 Audit transforms

These classify, justify, and compare.

Examples:

- observations,
- obligations,
- source pathologies,
- replay-product invariants,
- oracle comparisons,
- blame and section claims.

Rule:

> **Every nontrivial recovery or failure must become an explicit fact.**

---

## 9. Core semantic objects

A high-assurance LawVM should revolve around a small set of core objects.

### 9.1 Source objects

- `SourceBundle`
- source text spans
- token spans
- parsed clause spans

### 9.2 Syntax objects

- `ClauseSurface`
- clause nodes such as reference, label, text, meta, scope wrappers

### 9.3 Body objects

- `PayloadSurface`
- omission markers
- sparse payload slots
- source-local body shape

### 9.4 Meaning objects

- `ElaboratedIntent` / `ElaboratedGroup`
- coverage units / claims / gaps
- scoped effect intents

### 9.5 Execution objects

- `CanonicalIntent`
- `LegalOperation`
- `LegalAddress`
- `ExecutionContract`

### 9.6 Temporal objects

- `ProvisionVersion`
- `ProvisionTimeline`
- optional temporal overlay objects
- materialization specs

### 9.7 Epistemic objects

- `ParseWitness`
- `Observation`
- `Obligation`
- `CompileAdjudication`
- `SectionClaim`
- `EvidenceBundle`

The high-assurance design pressure is always toward making these objects more explicit and less stringly-typed.

---

## 10. Proof objects

LawVM can carry multiple kinds of proofs or certificates.

These need not all be theorem-prover proofs in the formal-methods sense. Many can be **checkable certificates** verified by small kernels.

### 10.1 Parse witnesses

A parse witness proves that a recovered operation or clause node came from a specific source span under a specific rule.

Typical contents:

- rule id,
- token span,
- source span,
- optional local environment.

Statement proved:

> “This syntactic object was derived from this exact source span by rule R.”

### 10.2 Preservation certificates

A preservation certificate proves that a transform preserved distinctions it claims to preserve.

Examples:

- ClauseAST round-trip,
- token-span preservation through filters,
- address preservation through lowering.

Statement proved:

> “This transform did not lose semantically relevant structure of class C.”

### 10.3 Ambiguity-resolution witnesses

These justify choices made during elaboration.

Examples:

- sparse slot assignment witness,
- chosen insertion anchor,
- chosen container ownership,
- VÄLIAIKAINEN extension chain selection.

Statement proved:

> “Among the admissible candidates, this one was chosen for these bounded reasons.”

In strict mode, some witnesses should never be needed. Their presence is itself a strict failure signal.

### 10.4 Invariant certificates

These prove that internal states remain inside the admissible model.

Examples:

- no duplicate exact-slot identity,
- valid occupancy transition,
- no impossible container membership,
- monotone ordering of sibling families,
- no conflicting active overlay state.

Statement proved:

> “The internal representation satisfies invariant set K after transform T.”

### 10.5 Temporal selection certificates

These justify PIT answers.

Examples:

- chosen background version,
- chosen temporary overlay,
- rejected expired version,
- governing vs in-force distinction,
- extension chain provenance.

Statement proved:

> “At date D, version V governs address A under selection rule set R.”

### 10.6 Lineage certificates

These justify how a current provision state descends from prior states.

Examples:

- affecting act chain,
- relabel lineage,
- replace/repeal/revival chain,
- source-to-version ancestry.

Statement proved:

> “This current version descends from these prior versions and these source acts.”

### 10.7 Negative proofs

These prove absence rather than presence.

Examples:

- no later act touched this section,
- no alternative active version exists,
- no source support exists for the oracle wording,
- no strict-clean derivation exists without heuristic H.

Statement proved:

> “No admissible counterexample of class C exists within the checked search space.”

### 10.8 Strictness certificates

These justify strict-clean compilation.

Examples:

- no target guessing,
- no fallback merge,
- no omission expansion,
- no context-dependent anchor recovery,
- no unresolved obligations.

Statement proved:

> “This source compiles under strict profile P without non-permitted recoveries.”

### 10.9 Comparative proofs

These justify comparisons against other artifacts.

Examples:

- replay vs oracle,
- replay vs timeline materialization,
- source payload prefers replay,
- oracle cutoff drift.

Statement proved:

> “The observed divergence is attributable to X and not merely an undiagnosed mismatch.”

### 10.10 Non-commensurability proofs

These prove that two artifacts should not be judged as directly equivalent.

Examples:

- editorial stub vs governing legal text,
- stale consolidated witness vs legal PIT,
- topology-drift mismatch vs semantic mismatch.

Statement proved:

> “These artifacts inhabit different comparison layers; direct equality is not the right test.”

---

## 11. Trusted kernels

A high-assurance LawVM should follow a “big generators, small checkers” style.

The trusted core should be smaller than the total pipeline.

### 11.1 Address kernel

Checks:

- canonical address form,
- parent/child consistency,
- exact-slot identity,
- allowed kind vocabulary under profile.

### 11.2 Occupancy kernel

Checks:

- valid transitions between absent / substantive / tombstone / scaffold,
- legality of replace / insert / repeal / reenact actions,
- slot-state contracts.

### 11.3 Tree invariant kernel

Checks:

- structural nesting invariants,
- sibling uniqueness,
- sibling ordering,
- no malformed replay tree states.

### 11.4 Temporal kernel

Checks:

- version ordering,
- commencement and expiry legality,
- background vs overlay selection,
- overlap constraints,
- query semantics for governing and in-force modes.

### 11.5 Lowering compatibility kernel

Checks:

- canonical intent agrees with legacy fields during migration,
- target families match target shape,
- action kinds match lowering families,
- required contracts are populated.

### 11.6 Evidence kernel

Checks:

- claims only use supported evidence,
- blame attribution satisfies stated prerequisites,
- statute-level summaries are derived from section-level facts.

### 11.7 Conformance kernel

Checks:

- stage-by-stage fixture conformance,
- round-trip preservation where claimed,
- no illegal drift across waists.

The design rule is:

> **Make candidate generation rich; make acceptance kernels small and auditable.**

---

## 12. Invariants by boundary

Every boundary should have explicit invariants.

### 12.1 Token / scanner boundary

Detect:

- destructive loss of structural tokens,
- back-reference loss,
- provenance stripping that crosses into real targets,
- statute-name stripping that eats live target carriers.

Enforce:

- only semantically dead spans may be deleted,
- otherwise emit tagged sentinels.

### 12.2 Clause surface boundary

Detect:

- duplicate exact targets,
- semantic collapse of renumber or move syntax,
- loss of heading / intro distinctions,
- ambiguous or partial verb-group consumption.

Enforce:

- distinct clause families remain distinct,
- parse witnesses are attached.

### 12.3 Payload surface boundary

Detect:

- malformed sparse payloads,
- unassigned payload slots,
- destructive shape-loss risk,
- container-membership mismatch,
- unresolved omission semantics.

Enforce:

- ambiguity remains explicit,
- no source-local transform invents live-state meaning.

### 12.4 Elaboration boundary

Detect:

- reliance on ambient master access,
- ambiguous slot assignments,
- context-dependent anchor resolution,
- uncovered-body recovery dependence,
- heuristic scope carry-forward.

Enforce:

- all live-state reads go through typed snapshots,
- unresolved ambiguity becomes an obligation.

### 12.5 Canonical operation boundary

Detect:

- missing target contract,
- unsupported action family,
- illegal occupancy expectations,
- silent lowering collapse.

Enforce:

- every executable op has typed action, target, and contract,
- apply does not reinterpret.

### 12.6 Replay / apply boundary

Detect:

- failed exact-target application,
- impossible occupancy transitions,
- fallback legacy dispatch during migration,
- replace-as-insert or insert-as-replace recovery.

Enforce:

- replay is operational only,
- execution failures are explicit facts.

### 12.7 Timeline boundary

Detect:

- non-monotone version ordering,
- conflicting overlaps,
- expiry-chain loss,
- illegal overlay combinations,
- background/overlay confusion.

Enforce:

- PIT selection is deterministic,
- temporary semantics are explicit.

### 12.8 Materialization boundary

Detect:

- drift from timeline state,
- unlabeled-node loss,
- base-template leakage into expired overlay state,
- duplicate or orphan children.

Enforce:

- materialized PIT is a faithful evaluation of the timeline substrate.

### 12.9 Evidence boundary

Detect:

- section facts lost in statute summaries,
- blame attribution without support,
- comparative mismatch misclassified as replay error,
- cache reuse across incompatible evidence schemas.

Enforce:

- evidence derives from explicit section-level artifacts,
- uncertainty and source pathology remain visible.

---

## 13. Error taxonomy

A high-assurance system should distinguish at least three classes of badness.

### 13.1 Impossible states

These are hard failures.

Examples:

- invariant violation,
- impossible occupancy transition,
- illegal overlap of incompatible active versions,
- canonical op missing a required contract field.

### 13.2 Suspicious but recoverable states

These are warnings or obligations.

Examples:

- duplicate target op,
- context-dependent scope carry-forward,
- target guessing required,
- uncovered-body recovery required,
- omission expansion required,
- fallback merge supplement used.

### 13.3 External non-commensurability or source-side problems

These are adjudications, not internal compiler warnings.

Examples:

- source pathology,
- oracle stale section,
- oracle cutoff drift,
- HTML/XML topology drift,
- source incompleteness.

The key rule is:

> **Warn at the layer that first knows something suspicious happened; do not wait for downstream fallout to infer it indirectly.**

---

## 14. Strict mode and quirks mode

LawVM must serve two worlds.

### 14.1 Quirks mode

For hostile historical corpora.

Allowed:

- recovery heuristics,
- source repairs,
- target-guessing under explicit policy,
- editorial-convention reproduction,
- best-effort comparison against imperfect external witnesses.

Requirement:

- every recovery is surfaced as evidence.

### 14.2 Strict mode

For future authoring and canonical publication.

Forbidden:

- hidden recovery,
- hidden target guessing,
- fallback semantics that silently invent meaning,
- irreducible ambiguity,
- destructive overwrite of temporal or provenance chains.

Requirement:

- failures are drafting or publication defects, not parser inconveniences.

The right generalization is:

- one canonical core,
- jurisdiction-specific strict profiles,
- explicit strictness certificates.

---

## 15. What LawVM can and cannot prove

### 15.1 What it can prove, conditionally

LawVM can increasingly prove statements of the form:

> Given source set S, profile P, and interpretation policy I, the temporal legal state T and PIT result Q were derived by certificate set C and satisfy invariant set K.

Concrete theorem shapes include:

- this clause parsed this way,
- this transform preserved target distinctions,
- this payload bound to these live slots,
- this operation was legal on this slot,
- this version governs at date D,
- this current wording descends from these acts,
- this divergence is due to oracle staleness rather than replay failure,
- this statute is strict-clean under profile P.

### 15.2 What it cannot prove by itself

LawVM cannot, by itself, prove:

- that the source corpus is complete,
- that official publication artifacts always faithfully encode legislative intent,
- that the oracle is legally authoritative,
- that higher-order doctrinal interpretation is correct,
- or that a court would resolve a genuine ambiguity the same way.

Those are outside its strongest theorem form.

---

## 16. High-assurance design rules

A future-proof LawVM should obey these rules.

1. **Preserve observations before interpreting them.**
2. **Interpret only through typed bounded environments.**
3. **Lower all execution assumptions into typed contracts.**
4. **Keep apply operational and boring.**
5. **Make time first-class and overlays explicit.**
6. **Emit every recovery, ambiguity, and pathology as a fact.**
7. **Judge section truth before statute summaries.**
8. **Prefer big generators with small trusted checkers.**
9. **Make strictness a profile, not a slogan.**
10. **Treat timelines / graph as the long-run authority.**

---

## 17. The endgame

The endgame is not “replace prose with code.”

It is:

- human-readable law remains,
- but official publication also emits canonical machine-readable change/state artifacts,
- and new laws are drafted so they compile cleanly under strict profiles.

In that world, LawVM serves three roles:

1. **historical recovery compiler** for legacy corpora,
2. **strict validator** for future drafting and publication,
3. **canonical temporal engine** for answering “what is the law at T?”

In short:

> **LawVM should evolve from a reverse-engineering tool into the reference compiler and validator for executable legal text-state.**

That is the theory.

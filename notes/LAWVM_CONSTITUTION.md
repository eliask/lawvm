# LawVM Constitution

Status: normative, implementation-near, and allowed to lead the code.
Purpose: define what LawVM is, which boundaries are hard, what the semantic authority is, what kinds of recovery are allowed, and what the system may honestly claim.

**LawVM is a compiler for hostile, underspecified legal delta sources.**

Not mainly a parser, not mainly a replay engine, not mainly an oracle diff tool.
A compiler. The source language is legislative amendment language and adjacent structured effect artifacts. The target is correct legal text-state across time, plus explicit evidence for how that state was derived.

---

## 1. Domain and scope

LawVM computes the **text-state layer** of law.

It answers questions like:
- what does provision P say at date D,
- which source act changed it,
- what temporary or permanent versions govern,
- and what chain of effects produced the current wording.

It does **not** by itself settle the full normative layer:
- purposive interpretation,
- doctrinal consequence,
- policy balancing,
- or court-like legal reasoning beyond the compiled text-state substrate.

Those may be built on top. They are not part of the core constitutional contract.

---

## 2. Two planes

LawVM computes two interleaved planes.

### Semantic plane

Legal meaning at the text-state level and legal state over time.

```text
SourceBundle
  -> ClauseSurface
  -> PayloadSurface
  -> ElaboratedIntent
  -> CanonicalEffects / CanonicalExecution
  -> Timeline / TemporalGraph
  -> PIT Materialization
```

### Epistemic plane

Why the system believes the semantic result.

```text
ParseWitnesses
  -> Observations
  -> Obligations
  -> Adjudications / Pathologies
  -> Claims
  -> EvidenceBundle
```

Every semantic stage emits epistemic artifacts via `PhaseResult` or an equivalent typed result surface.
The two planes are interleaved, not sequential.

---

## 3. Three hard waists

These are the stable contracts. Code above a waist may change freely without forcing changes below it, and vice versa.

### Waist 1: Clause surface

The typed clause AST for amendment instruction language.

Minimum role:
- preserve clause family distinctions,
- preserve verb grouping,
- preserve heading / intro / text-patch distinctions,
- preserve local provenance,
- avoid premature flattening.

Current implementation center: `src/lawvm/core/clause_ast.py`

### Waist 2: Payload surface

The amendment body after source-local normalization, before live-state elaboration.

Everything above PayloadSurface is pure amendment-artifact analysis.
Everything below requires a live replay snapshot.

Source-local above the waist includes:
- XML-to-IR conversion,
- wrapper cleanup,
- intro/list collapse,
- omission marker preservation,
- source-shape normalization.

Live-dependent below the waist includes:
- sparse omission alignment,
- slot ownership,
- row matching,
- container shadowing,
- broad-to-narrow semantic recovery.

Current implementation center: `src/lawvm/core/payload_surface.py`

### Waist 3: Canonical execution

The replay/runtime execution contract.

The stable fact is not the exact class name. The stable fact is this boundary:
**apply consumes typed canonical execution artifacts, not amendment XML, not PEG fields, and not ad hoc live-state rediscovery.**

Today this boundary is carried mainly by `ResolvedOp` plus typed intent/effect fields.
Longer term it should converge toward a canonical effect bundle whose execution projection is derived rather than hand-assembled.

Current implementation center: `src/lawvm/finland/ops.py`, `src/lawvm/core/canonical_intent.py`, effect-intent lowering.

### Internal bridge: Elaborated intent

Not a public waist but a first-class typed product.
It is the output of live-dependent meaning recovery and the input to canonical execution lowering.

Current implementation center: `src/lawvm/core/payload_surface.py` (`ElaboratedGroup`)

---

## 4. Semantic authority

### The semantic center is the canonical effect layer

The semantic authority is not the replay tree and not the rendered PIT text.
Those are derived views.

The semantic center is the typed effect layer:
- **structural effects**: replace / insert / repeal / relabel / move,
- **text effects**: explicit text-patch operations,
- **lifecycle effects**: commence / expire / suspend / revive / applicability.

Today these are still represented through several adjacent types (`LegalOperation`, `CanonicalIntent`, effect intents, `ResolvedOp`).
The constitutional direction is that they converge toward one canonical, witnessed effect bundle.

### Runtime views are projections, not independent authorities

- timelines / temporal graphs are a derived semantic projection,
- PIT materialization is a query on that temporal projection,
- Finlex-style editorial displays are presentation projections.

No lower layer may silently reinterpret or overwrite the meaning already established above it.

### One compiler, two outputs

Every compile should be understood as producing two logically separate things:
1. a semantic product (canonical effects and their temporal consequences),
2. an audit product (observations, obligations, recoveries, failures, strictness signals).

Quirks materialization may still succeed while strict verdict fails.
Strictness is computed from the audit path, not from end-text similarity alone.

### Two projections, one legal core

- **legal_pit**: the governing legal state at date D.
- **editorial_display**: presentation conventions, including residue and editorial notes, when a jurisdiction publishes them.

Editorial display must never contaminate legal PIT semantics.

---

## 5. Phase ownership rules

### Elaboration is snapshot-pure

Elaboration is the only phase that reads live replay state.
It must read from **typed snapshots**, not raw ambient master state.

The main snapshot families are:
- `TargetContext`
- `PayloadElaborationContext`
- `ReplayLookups`

**Arbitrary depth inside a bounded snapshot is fine. Arbitrary breadth over the live master is not.**

Four dependency classes for elaboration helpers:

| Class | Allowed input | Example |
|-------|---------------|---------|
| 1. Amendment-only | ClauseSurface, PayloadSurface | source-shape folds |
| 2. Local live subtree | `ctx.live_node` traversal | omission resolution |
| 3. Replay topology | `ctx.lookups` | membership, uniqueness |
| 4. Ambient master | **FORBIDDEN** below elaboration boundary | -- |

### Replay applies; it does not reinterpret

Replay owns:
- exact slot mutation,
- tombstones / placeholders,
- insertion order and family ordering,
- timeline intervals,
- PIT materialization,
- structural invariants.

Replay does **not** own:
- clause recovery,
- target guessing,
- omission meaning recovery,
- row matching,
- broad-to-narrow reinterpretation,
- or semantic rediscovery from weak legacy fields.

### Stage boundary = result boundary

Major stages return typed outputs plus explicit signals.
Helpers stay boring.
Stages are explicit.

Current implementation center: `src/lawvm/core/phase_result.py`

---

## 6. Temporal constitution

### Time is first-class

The core semantic product is temporal.
A provision may have multiple versions with different:
- enacted dates,
- effective dates,
- expiry dates,
- applicability predicates,
- and provenance.

PIT is not a property of source order. It is a query over temporal state.

### Temporary amendments are overlays, not just flat replacements

A temporary amendment does not permanently replace the background branch.
It creates a **temporary governing overlay** over the non-temporary background state.

At PIT date D:
1. compute the best non-temporary background state,
2. apply any temporary overlay active at D,
3. if neither exists, the slot is ABSENT.

This is a semantic rule even where current code still approximates it through flat version lists.

### Expired temporary insertions return to prior occupancy

For a temporary insertion with no background occupant, expiry returns the slot to ABSENT.
It does not create a permanent tombstone merely because an editorial display may retain history.

### Extension provenance must be preserved

When a later act extends the lifetime of a temporary effect, the compiler must preserve:
- the original temporary source,
- any later override acts,
- the final computed expiry,
- and the override chain as provenance.

Do not destructively overwrite lifecycle history and pretend only the final expiry ever existed.

---

## 7. Replay constitution

### Exact slot identity

Replay identity is based on exact slot identity:
- exact parent,
- exact kind,
- exact normalized label.

Separate from identity:
- ordered sibling family (`14a`, `14b`, `14c`),
- stem family (`14`, `14a`),
- presentation ranges (`14a–14c`).

Range folding is rendering. Identity is structural.

### Occupancy model

Every addressable slot has an occupancy class:
- **absent** — never existed or not yet created,
- **substantive** — live content,
- **tombstone** — repealed but still addressable,
- **scaffold** — temporary placeholder for ordering or structure.

Replace targets substantive slots.
Insert creates or fills slots according to contract.
Repeal converts substantive to tombstone where addressability is preserved.
Reenactment fills tombstones.

### Small kernels, big generators

LawVM should bias toward rich candidate generation and small trusted checkers.
Trusted kernels should remain smaller than the total pipeline.
Typical kernels include:
- address validation,
- occupancy validation,
- tree invariants,
- temporal selection,
- lowering compatibility,
- evidence admissibility.

---

## 8. Evidence constitution

### Section claims before statute summaries

A statute can simultaneously contain:
- proved replay bug,
- proved source pathology,
- oracle defect,
- editorial non-commensurability,
- unresolved residue.

Section-level truth first, statute-level banner second.

### Not every bad fact is a positive proof

Three evidence tiers:
- **positive explanation** — direct source/oracle/replay proof,
- **replay demotion** — defeats replay attribution without proving another cause,
- **risk signal** — suspicious but insufficient alone.

Some facts prove. Some facts only defeat. The system must not collapse those.

### Coverage is observed minus claimed

“Uncovered body” means `observed - claimed`, not `observed - applied`.
That removes circular dependence on the executor.

Coverage analysis is pre-apply elaboration.
Post-apply coverage checks are audits, not the main recovery semantics.

### Non-commensurability is a first-class conclusion

Sometimes two artifacts should not be judged by direct equality.
Examples:
- editorial residue vs governing legal text,
- stale consolidated witness vs legal PIT,
- topology drift vs semantic contradiction.

This is not a hack. It is part of honest high-assurance comparison.

---

## 9. Source honesty

### Malformedness must remain explicit

Source artifacts are hostile and underspecified.
The compiler must not silently normalize away problems.

- omission markers stay first-class at surface time,
- broad-to-narrow rewrite is elaboration, not extraction,
- unresolved cases stay unresolved,
- source pathologies are classified, not suppressed.

### Tag, don’t delete

The scanner classifies tokens; it does not destroy semantically live spans.
When a span is filtered, a tagged sentinel should survive whenever the span may matter downstream.

Destructive deletion is allowed only for semantically dead material with high confidence.
Anything context-sensitive should survive into parsing or later checking.

---

## 10. Conformance

### Phase-staged, not end-text-only

The conformance corpus should pin at least:
- clause surface,
- payload surface,
- elaboration,
- canonical execution artifacts,
- temporal behavior,
- replay/materialization,
- evidence behavior.

**The final materialized text must not be the only oracle.**

A de novo implementation should be able to pass the suite by reading only the assertions, not by archaeology into historical statutes.

### Boundary invariants must be explicit

Every major boundary should have named invariants and named error families.
Typical boundary classes:
- token / scanner boundary,
- clause surface boundary,
- payload surface boundary,
- elaboration boundary,
- canonical execution boundary,
- replay boundary,
- timeline boundary,
- materialization boundary,
- evidence boundary.

The system should detect both:
- impossible states that are hard failures,
- suspicious states that remain explicit warnings or obligations.

---

## 11. The conditional claim

LawVM does not prove arbitrary legal truths.
It makes a conditional, certificate-backed claim:

> **Given source set S, profile P, and interpretation policy I, LawVM derives temporal state T and PIT answers Q by certificate set C, and these satisfy invariant set K.**

This is the strongest honest theorem for a legal state compiler.

Where:
- **S** may be incomplete, and incompleteness must be recorded,
- **P** governs which recoveries are permitted,
- **I** governs ambiguity resolution where a jurisdiction still permits it,
- **T** is the compiled temporal state,
- **Q** are queries over T,
- **C** is the epistemic certificate set,
- **K** is the boundary and runtime invariant set.

What LawVM cannot prove by itself:
- that the corpus is complete,
- that published XML perfectly reflects drafter intent,
- that a consolidated oracle is authoritative,
- or that doctrinal interpretation beyond text-state is correct.

---

## 12. Migration rules

### One semantic owner per family

For any amendment family:
- old code may survive as adapter,
- but old and new code must not both own the same semantic decision for long.

### Migration order

1. finish payload/elaboration boundaries for active families,
2. remove corresponding apply-time rediscovery,
3. emit typed observations and obligations at the stage that first knows them,
4. move section evidence to section-first claim resolution,
5. migrate supplement-heavy clause families into typed clause surfaces,
6. tighten temporal and runtime kernels,
7. converge toward canonical effect authority.

### Current vs target must stay distinguishable

The constitution may lead the code, but it must not silently pretend that target-state constructs are already fully implemented.
Where current implementation and target architecture differ, that difference should be explicit.

---

## 13. The thirteen rules

1. LawVM is a compiler for hostile legal deltas, not a best-effort parser.
2. LawVM computes the legal text-state layer, not the full normative layer.
3. Clause surface, payload surface, and canonical execution are hard waists.
4. The semantic center is the canonical effect layer; replay trees and displays are derived views.
5. Quirks materialization and strict verdict are orthogonal outputs.
6. Elaboration is snapshot-pure and the only live-dependent semantic recovery phase.
7. Replay applies; it does not reinterpret.
8. Temporary effects are overlays; expired temporary inserts return to prior occupancy.
9. Exact slot identity governs replay occupancy and addressability.
10. Every phase emits facts; facts feed evidence.
11. Section claims come before statute summaries.
12. Conformance is phase-staged, and source malformedness must remain explicit.
13. LawVM’s claim is conditional: given S, P, I, derive T and Q by C satisfying K.

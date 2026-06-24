> **Status (2026-06-22):** Current. Explanatory/navigational crosswalk (defers term definitions to LAWVM_PIPELINE_CONTRACT §11). All cited contract sections (§2/§3/§11) and impl-center code paths verified present; no stale §1.13/PEG3 refs.

# LawVM Architecture Index

Status: current compact architecture map for the v0.1 release line.

For the full current spec map, start with [SPEC_INDEX.md](SPEC_INDEX.md).

## One-Sentence Architecture

LawVM is a compiler from hostile legal delta sources to a proof-carrying
temporal legal-state machine.

## Terminology reconciliation (crosswalk)

> **This index defers to [LAWVM_PIPELINE_CONTRACT.md](LAWVM_PIPELINE_CONTRACT.md) §11 for all term *definitions*.** This section is the navigation/crosswalk map: it tells readers how the coarse historical framing (used here, in [THEORY_OF_LAWVM.md](THEORY_OF_LAWVM.md), and in older docs) maps onto the canonical terms the contract pins. Where this index and the contract disagree, the contract wins.

### Four orthogonal views (not a hierarchy)

plane, phase, waist, and seam describe the same pipeline four independent ways — they are orthogonal, not nested levels (definitions: contract §11.1):

- **plane** = an authority/truth domain (a *kind of truth* a value carries).
- **phase** = a *verb*: a process step the compiler runs ([AGENTS.md](../AGENTS.md) §3.1).
- **waist** = a *noun*: the canonical typed artifact boundary passed between phases.
- **seam** = a **stable consumer/interoperability contract** over one or more waists/projections (e.g. provision-state seam, certificate/checker seam, MeVM seam) — *not* a "migration crossing".

Phases are verbs; waists are the typed nouns produced between them. A phase consumes one waist and emits another.

### Plane crosswalk: 2 constitutional ↔ 6 operational

Two constitutional planes (the values-vs-accounts split) **refine into** six operational planes (to prevent authority bleed). This is a refinement, not a replacement. Authority is **not** a plane — it is a firewall surface across planes.

| Constitutional plane (alias) | Refines into operational planes (contract §3) |
|---|---|
| **state/value** *(historical: semantic)* | source, surface, legal-state |
| **proof/accounting** *(historical: epistemic)* | evidence, projection, overlay |

The semantic→source/surface/legal-state split exists so that re-deriving meaning from a lossier representation after a typed owner exists is a type error (no representation regression). projection and overlay are first-class because each carries its own invariant (a projection must be re-derivable from a committed dossier; an external provider may help LawVM see more but never become the reason it claims to know — the determinism firewall). The central invariant governs exactly the crossings *into* the legal-state plane.

### Waist crosswalk: 5 replay-core ↔ 10 full-pipeline

The historical **five** hard waists are the **replay-core** subset; the **ten** (contract §2) are the **full-pipeline** waists. The ten do **not** "replace" the five — they refine and extend them.

| Historical replay-core waist | Full-pipeline equivalent (contract §2) |
|---|---|
| Clause waist | surface_syntax / surface_families (ClauseAST / SurfaceClause) |
| Payload waist | canonical_op input (payload/elaboration) |
| Canonical-op waist | canonical_op (CanonicalEffect / LegalOperation) |
| Temporal waist | timeline_materialization (TemporalEvent / ProvisionTimeline) |
| Authority waist | apply_receipt + certificate (ExecutionAuthorization / ResolverBinding / WriteReceipt / certificate_status) |

The fine waists the full list adds beyond the replay-core lens — **source_identity**, **token_structure**, **certificate**, **projection**, **overlay** — are exactly the boundaries where silent drops/guesses were found (see the leak ledger), which is why they were promoted to named waists.

### Graph-name rule

Graph names are **not** interchangeable. **A graph's name must say which plane it belongs to and whether it is producer, proof, or projection.**

- **SourceSyntaxGraph** (nickname: *forest*) — token/source-total construction graph over source text; owns syntactic/surface parse accounting; **producer**, does not authorize replay. Prefer `SourceSyntaxGraph` in normative prose, not "forest".
- **LegalSurfaceGraph** — graph of explicit source-surface facts (references, definitions, terms, temporal expressions, actor/modal frames, conditions, exceptions, residuals); static-analysis, surface-only; **not** replay authority. See [LEGAL_SURFACE_GRAPH.md](LEGAL_SURFACE_GRAPH.md).
- **ProvenanceGraph** — graph of assertions, attestations, sources, reviews, retractions, dependencies (proof/accounting plane); does not self-authorize replay.
- **TransitionGraph** — **projection** over temporal legal-state transitions; not the replay source of truth unless backed by a certificate/trace root.

## Current Public Planes

The load-bearing axis is two interleaved planes (this framing and THEORY_OF_LAWVM.md §6). These are the two **constitutional** planes; the canonical names are **state/value** and **proof/accounting** (contract §11.2), and "semantic"/"epistemic" below are the historical aliases:

- **Semantic plane** *(canonical: state/value):* source bundle -> clause/effect surface -> payload surface
  -> elaborated intent -> canonical operations/effects -> timelines ->
  point-in-time materialization.
- **Epistemic plane** *(canonical: proof/accounting):* parse witnesses -> observations -> obligations ->
  source pathologies/adjudications -> evidence bundles -> strict verdicts.

The semantic output without the epistemic output is not enough. A replay result
must be able to explain which source facts support it and where uncertainty or
non-commensurability remains.

These two planes **refine into the six type-distinct planes** that the pipeline
contract pins ([LAWVM_PIPELINE_CONTRACT.md](LAWVM_PIPELINE_CONTRACT.md) §3, [AGENTS.md](../AGENTS.md) §2.10), which is the
canonical set: the **semantic** plane splits into **source / surface / legal-state**
(so that re-deriving meaning from a lossier representation after a typed owner
exists is a type error — no representation regression); the **epistemic** plane
is **evidence/proof**; and **projection** and **overlay/enrichment** are promoted
to first-class planes because each carries its own invariant (a projection must be
re-derivable from a committed dossier; an external provider may help LawVM see more
but never become the reason it claims to know — the determinism firewall). The
central invariant — evidence/overlay never silently becomes legal-state authority —
governs exactly the crossings between the epistemic/overlay planes and the
semantic/legal-state planes.

## Hard Waists

The canonical waist enumeration is the ten-row table in
[LAWVM_PIPELINE_CONTRACT.md](LAWVM_PIPELINE_CONTRACT.md) §2 (source_identity, token_structure,
surface_syntax, surface_families, canonical_op, apply_receipt,
timeline_materialization, certificate, projection, overlay), each with a canonical
input type, output type, coverage certificate, and authority status. The coarse
historical waists below remain a useful reading lens and map onto that table:

- **Clause surface** (-> surface_syntax / surface_families): typed representation
  of operative amendment language.
- **Payload surface** (-> canonical_op input): source-local amendment body shape
  before live-state elaboration.
- **Canonical execution** (-> canonical_op / apply_receipt): typed operation/effect
  contract consumed by replay.
- **Temporal graph/timeline** (-> timeline_materialization): executable state over
  time, including PIT materialization.
- **Authority/branch axis** (the authority *surface* carried across waists, not a
  waist itself): enacted law remains the default materialization context;
  draft/proposal/consultation claims live on explicit branches.

The fine waists the contract adds beyond this lens — **source_identity**,
**token_structure**, **certificate**, **projection**, **overlay** — are exactly the
boundaries where silent drops/guesses were found (see the leak ledger), which is
why they were promoted to named waists.

The **phases** in [AGENTS.md](../AGENTS.md) §3.1 (Acquire -> Clean -> Parse -> Extract -> Normalize
-> Elaborate -> Lower -> Replay -> Compile-timelines -> Adjudicate -> Emit) are the
verb sequence; the waists are the typed nouns produced between those phases. One
pipeline, two views.

The target direction is that replay applies typed contracts and does not
rediscover legal meaning from raw source text.

For Finland, the next hard source boundary is
[FINLAND_XML_INGEST_ONLY_SOURCE_MODEL.md](FINLAND_XML_INGEST_ONLY_SOURCE_MODEL.md):
XML/lxml is an acquisition/model-building surface only, while ordinary
parse/elaborate/lower/apply/temporal phases consume typed source units,
payload IR, witnesses, and temporal/johto surfaces.

## Current Implementation Centers

- `src/lawvm/core/ir.py` — legal addresses, IR nodes, operations, timelines.
- `src/lawvm/core/clause_ast.py` — shared clause-surface structures.
- `src/lawvm/core/payload_surface.py` — payload-surface waist.
- `src/lawvm/core/canonical_intent.py` — typed canonical operation intent.
- `src/lawvm/core/phase_result.py` — stage output plus findings/events.
- `src/lawvm/core/timeline*.py` — timelines, selection, lineage, materialization.
- `src/lawvm/core/branch_authority.py` — authority layers, branch contexts, and branch
  graph edges.
- `src/lawvm/core/branch_projection.py` — branch impact projection payloads.
- `src/lawvm/finland/` — deepest reference frontend and replay pipeline.
- `src/lawvm/tools/cli.py` — developer CLI entrypoint.

## Frontend Roles

A jurisdiction frontend owns:

- source acquisition and archive assumptions;
- local source cleaning and pathology classification;
- formula/clause/effect extraction;
- payload normalization;
- live-state elaboration;
- lowering to core operations/effects;
- jurisdiction-specific oracle/witness adjudication.

Core owns:

- legal address and IR primitives;
- generic replay/tree/timeline contracts;
- shared findings/evidence contracts;
- migration and temporal semantics as they become jurisdiction-neutral.

## v0.1 Reality

The architecture is partially realized. Core has real typed contracts and
Finland exercises them deeply, but there are still migration seams:

- Finland `AmendmentOp` and `ResolvedOp` remain compatibility shells.
- Legacy apply dispatch still exists for bounded cases.
- Some temporal and migration semantics are still projected through Finland
  replay products while core ownership matures.
- Finland should adopt the UK evidence/workqueue discipline for its next
  frontier loop: non-executable claim templates, source witness hashes,
  manual-frontier status, and typed residual classes before any unsafe replay
  shortcut.
- CLI and serialized outputs are useful but not stable public APIs.

This is acceptable for v0.1 as an alpha / research preview, provided it remains
explicit in release docs.

## Reading Order

1. [../README.md](../README.md)
2. [../RELEASE_V0_1.md](../RELEASE_V0_1.md)
3. [SPEC_INDEX.md](SPEC_INDEX.md)
4. [LAWVM_CONSTITUTION.md](LAWVM_CONSTITUTION.md)
5. [THEORY_OF_LAWVM.md](THEORY_OF_LAWVM.md)
6. [CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md)
7. [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
8. [LEGAL_SURFACE_GRAPH.md](LEGAL_SURFACE_GRAPH.md)
9. [ROADMAP_V1_0.md](../ROADMAP_V1_0.md)

## Historical Material

Pre-v0.1 plans, dated audits, old work queues, and exploratory design memos are
not part of the public v0.1 source tree. Treat this index and
[SPEC_INDEX.md](SPEC_INDEX.md) as the current architecture contract.

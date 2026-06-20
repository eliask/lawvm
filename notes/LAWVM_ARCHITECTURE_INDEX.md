# LawVM Architecture Index

Status: current compact architecture map for the v0.1 release line.

For the full current spec map, start with [SPEC_INDEX.md](SPEC_INDEX.md).

## One-Sentence Architecture

LawVM is a compiler from hostile legal delta sources to a proof-carrying
temporal legal-state machine.

## Vocabulary: planes, waists, phases, seams

These four words describe the same pipeline at different granularities. The
normative enumeration of all of them is [LAWVM_PIPELINE_CONTRACT.md](LAWVM_PIPELINE_CONTRACT.md);
this section is the bridge between the coarse framing used here and in
[THEORY_OF_LAWVM.md](THEORY_OF_LAWVM.md) and the fine framing the contract pins.

- **Planes** = the *kinds of truth* a value carries (its type discipline).
- **Waists** = the *narrow typed boundaries* between stages (the canonical
  input/output types).
- **Phases** = the *verb sequence* the compiler runs (what it does).
- **Seams** = specific plane/waist crossings targeted for conversion to the
  `StageResult` shape (the active enforcement work; see
  [ARCHITECTURE_LEAK_LEDGER.md](ARCHITECTURE_LEAK_LEDGER.md)).

## Current Public Planes

The load-bearing axis is two interleaved planes (this framing and THEORY_OF_LAWVM.md §6):

- **Semantic plane:** source bundle -> clause/effect surface -> payload surface
  -> elaborated intent -> canonical operations/effects -> timelines ->
  point-in-time materialization.
- **Epistemic plane:** parse witnesses -> observations -> obligations ->
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
- `src/lawvm/core/authority.py` — authority layers, branch contexts, and branch
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
8. [ROADMAP_V1_0.md](../ROADMAP_V1_0.md)

## Historical Material

Pre-v0.1 plans, dated audits, old work queues, and exploratory design memos are
not part of the public v0.1 source tree. Treat this index and
[SPEC_INDEX.md](SPEC_INDEX.md) as the current architecture contract.

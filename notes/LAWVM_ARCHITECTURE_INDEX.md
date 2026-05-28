# LawVM Architecture Index

Status: current compact architecture map for the v0.1 release line.

For the full current spec map, start with [SPEC_INDEX.md](SPEC_INDEX.md).

## One-Sentence Architecture

LawVM is a compiler from hostile legal delta sources to a proof-carrying
temporal legal-state machine.

## Current Public Planes

LawVM has two interleaved planes:

- **Semantic plane:** source bundle -> clause/effect surface -> payload surface
  -> elaborated intent -> canonical operations/effects -> timelines ->
  point-in-time materialization.
- **Epistemic plane:** parse witnesses -> observations -> obligations ->
  source pathologies/adjudications -> evidence bundles -> strict verdicts.

The semantic output without the epistemic output is not enough. A replay result
must be able to explain which source facts support it and where uncertainty or
non-commensurability remains.

## Hard Waists

- **Clause surface:** typed representation of operative amendment language.
- **Payload surface:** source-local amendment body shape before live-state
  elaboration.
- **Canonical execution:** typed operation/effect contract consumed by replay.
- **Temporal graph/timeline:** executable state over time, including PIT
  materialization.
- **Authority/branch axis:** enacted law remains the default materialization
  context; draft/proposal/consultation claims live on explicit branches.

The target direction is that replay applies typed contracts and does not
rediscover legal meaning from raw source text.

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

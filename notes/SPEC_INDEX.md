# LawVM Spec Index

Status: current public/spec index for the v0.1 release line.
Kind: normative map plus compact descriptive inventory.

This file points to the documents that should be treated as current for the
public v0.1 release line. Historical internal work queues and investigation
packets are not part of the public source tree.

## Spec Kinds

LawVM separates three kinds of documents:

- **Normative:** cleanroom contracts and phase boundaries. These define the
  target architecture.
- **Descriptive:** current implementation state, migration seams, and active
  frontend status.
- **Explanatory:** theory, design rationale, and long-term proof direction.

If a new idea changes the target, update a normative spec. If it documents
current implementation reality, update a descriptive note. If it only motivates
the architecture, keep it explanatory.

## Release-Facing Docs

- [../RELEASE_V0_1.md](../RELEASE_V0_1.md)
- [../ROADMAP.md](../ROADMAP.md)
- [../ROADMAP_V0_1.md](../ROADMAP_V0_1.md)
- [../ROADMAP_V1_0.md](../ROADMAP_V1_0.md)
- [../CHANGELOG.md](../CHANGELOG.md)
- [../docs/getting-started.md](../docs/getting-started.md)
- [../docs/benchmark-methodology.md](../docs/benchmark-methodology.md)
- [../docs/jurisdictions.md](../docs/jurisdictions.md)
- [../README.md](../README.md)

## Normative Core

- [LAWVM_CONSTITUTION.md](LAWVM_CONSTITUTION.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [COMPILER_OBSERVATION_STREAM.md](COMPILER_OBSERVATION_STREAM.md)
- [REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)
- [SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md](SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md)
- [CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md)

## Finland Reference Frontend

- [FINLAND_ARCHITECTURAL_COHERENCE.md](FINLAND_ARCHITECTURAL_COHERENCE.md)
- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [FINLAND_CLAUSE_AST_SPEC.md](FINLAND_CLAUSE_AST_SPEC.md)
- [FINLAND_PAYLOAD_IR_SPEC.md](FINLAND_PAYLOAD_IR_SPEC.md)
- [FINLAND_ELABORATION_RULES.md](FINLAND_ELABORATION_RULES.md)
- [FINLAND_ELABORATED_GROUP_SPEC.md](FINLAND_ELABORATED_GROUP_SPEC.md)
- [FINLAND_SPARSE_SUBSECTION_SLOT_SPEC.md](FINLAND_SPARSE_SUBSECTION_SLOT_SPEC.md)

## Other Frontends

- [ESTONIA_ARCHITECTURAL_COHERENCE.md](ESTONIA_ARCHITECTURAL_COHERENCE.md)
- [ESTONIA_FRONTEND_LIVING_SPEC.md](ESTONIA_FRONTEND_LIVING_SPEC.md)
- [UK_ARCHITECTURAL_COHERENCE.md](UK_ARCHITECTURAL_COHERENCE.md)
- [UK_REPLAY_LIVING_SPEC.md](UK_REPLAY_LIVING_SPEC.md)
- [UK_REPLAY_REGIME_CONTRACT.md](UK_REPLAY_REGIME_CONTRACT.md)
- [NORWAY_ARCHITECTURAL_COHERENCE.md](NORWAY_ARCHITECTURAL_COHERENCE.md)
- [SWEDEN_ARCHITECTURAL_COHERENCE.md](SWEDEN_ARCHITECTURAL_COHERENCE.md)
- [OPEN_LAW_FRONTEND_SPEC.md](OPEN_LAW_FRONTEND_SPEC.md)
- [OPEN_LAW_CORPUS_REPLAY_PLAN.md](OPEN_LAW_CORPUS_REPLAY_PLAN.md)

## Evidence and Verification

- [VERIFICATION_PROPERTY_MAP.md](VERIFICATION_PROPERTY_MAP.md)
- [AUDITOR_SPECS.md](AUDITOR_SPECS.md)

## Theory and Long-Term Design

- [THEORY_OF_LAWVM.md](THEORY_OF_LAWVM.md)
- [LAWVM_COMPILER_DIFFICULTY.md](LAWVM_COMPILER_DIFFICULTY.md)
- [PROOF_BOUNDARY.md](PROOF_BOUNDARY.md)
- [PROOF_ALGEBRA.md](PROOF_ALGEBRA.md)
- [PROOF_CLAIMS_ALGEBRA.md](PROOF_CLAIMS_ALGEBRA.md)
- [EVIDENCE_INFERENCE_MODEL.md](EVIDENCE_INFERENCE_MODEL.md)
- [CONDITIONAL_ENACTMENT_AND_TEMPORAL_EFFECTS.md](CONDITIONAL_ENACTMENT_AND_TEMPORAL_EFFECTS.md)
- [ADVERSARIAL_AUDIT_PASS.md](ADVERSARIAL_AUDIT_PASS.md)

## Current Implementation Maps

- [LAWVM_ARCHITECTURE_INDEX.md](LAWVM_ARCHITECTURE_INDEX.md)
- [LAWVM_STACK_MAP.md](LAWVM_STACK_MAP.md)

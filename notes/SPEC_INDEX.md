> **Status (2026-06-22):** Current-with-noted-drift. Normative map + descriptive inventory. All linked paths resolve, but: line 101 'AGENTS.md §1.13' is stale (now §1.11/§1.12, §2.4); and three normative docs AGENTS.md itself cites are missing from the index — DISCIPLINE_GATES.md, UNIFIED_BENCH_CONTRACT.md, FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md (plus NZ/NO/SE status docs, indexed only for US).

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
- [../docs/open-law-demo.md](../docs/open-law-demo.md)
- [../docs/benchmark-methodology.md](../docs/benchmark-methodology.md)
- [../docs/jurisdictions.md](../docs/jurisdictions.md)
- [../docs/security-privacy.md](../docs/security-privacy.md)
- [../README.md](../README.md)

## Normative Core

- [LAWVM_CONSTITUTION.md](LAWVM_CONSTITUTION.md)
- [LAWVM_PIPELINE_CONTRACT.md](LAWVM_PIPELINE_CONTRACT.md) — the checkable constitution of the pipeline: the central invariant + four sub-invariants (no silent drop/guess/authority-promotion/representation-regression), the 10 waists with canonical input/output/coverage/authority types, the six type-distinct planes, the no-reach-back rule + witness exception, closed conservation vocabularies, guard-liveness as a registry rule, the authority firewall in types, identity discipline, certificate as destination. Backlog measured against it: `ARCHITECTURE_LEAK_LEDGER.md`
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [COMPILER_OBSERVATION_STREAM.md](COMPILER_OBSERVATION_STREAM.md)
- [LAWVM_PROOF_SURFACES.md](LAWVM_PROOF_SURFACES.md)
- [FRONTEND_CAPABILITY_MATRIX.md](FRONTEND_CAPABILITY_MATRIX.md)
- [MANUAL_COMPILATION_CLAIMS.md](MANUAL_COMPILATION_CLAIMS.md)
- [REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)
- [SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md](SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md)
- [CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md)
- [LEGAL_BRANCH_AND_AUTHORITY_AXIS.md](LEGAL_BRANCH_AND_AUTHORITY_AXIS.md)
- [SEAM_SPEC_PROVISION_STATE.md](SEAM_SPEC_PROVISION_STATE.md)
- [CERTIFICATE_SCHEMA_V0.md](CERTIFICATE_SCHEMA_V0.md) — temporal-dossier certificate, hash hierarchy, residue honesty, checker v0 contract
- [CERTIFIED_TREE_TRANSITION_TRACE_V0.md](CERTIFIED_TREE_TRANSITION_TRACE_V0.md) — replayable transition grammar: base tree, content blobs, action semantics, state-root/checkpoint semantics
- [APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md](APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md) — the semantic apply waist: ScopedTargetResolver/ResolverBinding, fallback rungs, WriteReceipt, ObservedWriteAudit, occupancy contract, transition-leaf production
- [DISCIPLINE_GATES.md](DISCIPLINE_GATES.md) — normative (cited by AGENTS.md §4)
- [UNIFIED_BENCH_CONTRACT.md](UNIFIED_BENCH_CONTRACT.md) — normative (cited by AGENTS.md §4)
- [FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md](FI_JOHTOLAUSE_SURFACE_PARSER_CONTRACT.md) — normative (cited by AGENTS.md §4)

## Finland Reference Frontend

- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [FINLAND_CLAUSE_AST_SPEC.md](FINLAND_CLAUSE_AST_SPEC.md)
- [FINLAND_PAYLOAD_IR_SPEC.md](FINLAND_PAYLOAD_IR_SPEC.md)
- [FINLAND_XML_INGEST_ONLY_SOURCE_MODEL.md](FINLAND_XML_INGEST_ONLY_SOURCE_MODEL.md)
- [FINLAND_ELABORATION_RULES.md](FINLAND_ELABORATION_RULES.md)
- [FINLAND_ELABORATED_GROUP_SPEC.md](FINLAND_ELABORATED_GROUP_SPEC.md)
- [FINLAND_SPARSE_SUBSECTION_SLOT_SPEC.md](FINLAND_SPARSE_SUBSECTION_SLOT_SPEC.md)
- [FI_AMENDMENT_DRAFTING_GRAMMAR.md](FI_AMENDMENT_DRAFTING_GRAMMAR.md) — best-practice johtolause drafting guide derived from the per-rule register tiers (32 canonical / 7 accepted / 22 discouraged / 5 archaic)
- [FI_REFERENCE_CATALOGUE.md](FI_REFERENCE_CATALOGUE.md) — living catalogue of Finnish citation/reference families: resolution-status ladder + determinism tiers (T1/T2/T3), the typed-overlay IR model (references as H1 of the Legal Surface Algebra), per-family table + detail cards, recognizer inventory (wired vs standalone), typed-primitive/status map, registry/convention dependencies, verification matrix, coverage ledger
- [FINLAND_PERIODIC_TABLE.md](FINLAND_PERIODIC_TABLE.md) — Finland abstraction axes (phase/structure/time/provenance/evidence/instrumentation); filled cells, open holes, proof-projector split map; machine catalog in `src/lawvm/finland/periodic_table.py`
- [LEGAL_SURFACE_GRAPH.md](LEGAL_SURFACE_GRAPH.md) — canonical spec for the Legal Surface Graph: node/edge model + source-anchored identity, lens producers, the surface_only/replay_authorized authority firewall, typed residue + token-partition/coverage certificate, the SourceSyntaxGraph forest (totality certifier, not yet sole producer), bitemporal broken-ref detection, and the projection consumers. The spine the catalogue's H1 plugs into. Core algebra `src/lawvm/core/legal_surface_{graph,assembler,lens,lints,tokens}.py`; Finland lenses `src/lawvm/finland/legal_surface/`; E2E entry `lawvm surface-graph <id>` (full code map in the spec).

## Other Frontends

- [ESTONIA_FRONTEND_LIVING_SPEC.md](ESTONIA_FRONTEND_LIVING_SPEC.md)
- [UK_FRONTEND_ELABORATION_ARCHITECTURE.md](UK_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [UK_REPLAY_LIVING_SPEC.md](UK_REPLAY_LIVING_SPEC.md)
- [UK_REPLAY_REGIME_CONTRACT.md](UK_REPLAY_REGIME_CONTRACT.md)
- [UK_OFFICIAL_DRAFTING_SOURCE_LEDGER.md](UK_OFFICIAL_DRAFTING_SOURCE_LEDGER.md) — per-statute UK official drafting-source rules
- [US_LAWVM_STATUS.md](US_LAWVM_STATUS.md) — U.S. federal frontend status/limits/roadmap: witness-anchored dry-run over keyless govinfo PLAW USLM source + USC annual-edition htm oracle; coverage, lowering-gap residual classes, geo-block reality
- [NEW_ZEALAND_LAWVM_STATUS.md](NEW_ZEALAND_LAWVM_STATUS.md) — New Zealand frontend status/limits/roadmap
- [NORWAY_LAWVM_STATUS.md](NORWAY_LAWVM_STATUS.md) — Norway frontend status/limits/roadmap
- [SWEDEN_LAWVM_STATUS.md](SWEDEN_LAWVM_STATUS.md) — Sweden frontend status/limits/roadmap
- [OPEN_LAW_FRONTEND_SPEC.md](OPEN_LAW_FRONTEND_SPEC.md)
- [OPEN_LAW_REGIME.md](OPEN_LAW_REGIME.md)

## Evidence and Verification

- [VERIFICATION_PROPERTY_MAP.md](VERIFICATION_PROPERTY_MAP.md)
- [CORPUS_REPLAY_EVIDENCE_CONTRACT.md](CORPUS_REPLAY_EVIDENCE_CONTRACT.md)
- [JURISDICTION_CLI_TOOLING_CONTRACT.md](JURISDICTION_CLI_TOOLING_CONTRACT.md)

## Theory and Long-Term Design

- [THEORY_OF_LAWVM.md](THEORY_OF_LAWVM.md)
- [LAWVM_COMPILER_DIFFICULTY.md](LAWVM_COMPILER_DIFFICULTY.md)
- [PROOF_BOUNDARY.md](PROOF_BOUNDARY.md)
- [PROOF_ALGEBRA.md](PROOF_ALGEBRA.md)
- [PROOF_CLAIMS_ALGEBRA.md](PROOF_CLAIMS_ALGEBRA.md)
- [EVIDENCE_INFERENCE_MODEL.md](EVIDENCE_INFERENCE_MODEL.md)
- [CONDITIONAL_ENACTMENT_AND_TEMPORAL_EFFECTS.md](CONDITIONAL_ENACTMENT_AND_TEMPORAL_EFFECTS.md)

## Current Implementation Maps

- [LAWVM_ARCHITECTURE_INDEX.md](LAWVM_ARCHITECTURE_INDEX.md)
- [LAWVM_STACK_MAP.md](LAWVM_STACK_MAP.md)
- [REGEX_TO_GRAMMAR_MIGRATION.md](REGEX_TO_GRAMMAR_MIGRATION.md) — when string matching stays regex (lint + prefilter) vs becomes a named recognizer/spec; applies AGENTS.md §1.11/§1.12 (regex/recognizer firewall) + §2.4
- [IMPLEMENTATION_DIVERGENCE_LEDGER.md](IMPLEMENTATION_DIVERGENCE_LEDGER.md) — current target-vs-implementation gaps + active work queue
- [ARCHITECTURE_LEAK_LEDGER.md](ARCHITECTURE_LEAK_LEDGER.md) — EV-ranked backlog of representation/typing/authority leaks vs `LAWVM_PIPELINE_CONTRACT.md` (audit-and-enforce, not rewrite); 27 ranked sites + 2 CI-gate specs + 2 seam candidates from the e2e architecture-coherence audit
- [LAWVM_INVARIANT_GENERATOR_V0.md](LAWVM_INVARIANT_GENERATOR_V0.md) — the METHOD the registry is generated from (Pro-blessed 2026-06-23): "no public claim without a live accounting path"; the 12-field InvariantSpec, 6 axes (planes × waists × object-kinds × transform-verbs × failure-classes × public-claims), the 12-question generator, the 9-bucket stopping rule (forbidden = `implicit convention`), and 15 under-covered families to fold (INV-META/POL/SRC-LINEAGE/TIME-AXIS/SCOPE/SEL/WRITE/SCHED/PROJ/OVL/SIG/CORPUS/NEG/DET/REGEX)
- [INVARIANT_DISCIPLINE_AND_PRECEDENT.md](INVARIANT_DISCIPLINE_AND_PRECEDENT.md) — standalone account of the total-invariant-mining discipline: precise statement, its lineage (Design-by-Contract, HAZOP/FMEA, DO-178C/ISO-26262 traceability, proof-carrying code & CompCert/seL4, assertion-based auditing, Jepsen/mutation-testing), what is genuinely novel (HAZOP-generator over a compiler's typed waists + proof-carrying certs + per-unit totality, transferred to law; guard-liveness as a non-optional completion field), how it manifests in LawVM artifacts, and honest limits (bounds the claim surface not the world; evidence- not proof-carrying; oracle-fallible floor)
- [LAWVM_AUDIT_INVARIANT_REGISTRY.md](LAWVM_AUDIT_INVARIANT_REGISTRY.md) — complete a-priori audit/invariant coverage map: 78 distinct audits across 6 planes from 8 generative axes (planes/waists/§1.x/prime-directive-facts/temporal-determinism/certificate/meta-overlay/cross-jurisdiction); 31 implemented / 27 partial / 20 open, 44 new beyond the UK roadmap; ranked OPEN next-tier roadmap + adversarial completeness self-critique. Subsumes the UK-checkout `LAWVM_AUDIT_REGISTRY_ROADMAP.md`
- [FINLAND_COVERAGE_GAP_REPORT.md](FINLAND_COVERAGE_GAP_REPORT.md) — Finland projection freshness / coverage snapshot

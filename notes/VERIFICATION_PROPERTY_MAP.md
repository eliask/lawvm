# Verification Property Map

Status: living record.
Kind: descriptive.

Maps each verified property to its checker, when it runs, and what
bug class it catches. Addresses Pro adversarial review attack #23
("verification stack = listware without property mapping").

## Property → Tool → Gate

| Property | Tool | Artifact | When | Blocking? | Bug class caught |
|----------|------|----------|------|-----------|-----------------|
| No undefined names | ruff F821 | src/lawvm/, tests/ | CI | Yes | NameError at import time |
| No unused imports | ruff F401 | src/lawvm/, tests/ | Yes | Yes | Dead code hiding real imports |
| No duplicate set elements | ruff B033 | src/lawvm/ | CI | Yes | Silent logic error (PEG parser) |
| No useless conditionals | ruff RUF034 | src/lawvm/ | CI | Yes | Dead branch masking incomplete impl |
| Type errors (ty) | ty check | src/lawvm/ | CI | Yes | Type mismatch, None access, wrong arg |
| Boundary invariants | pytest test_boundary_guards.py | 6 architectural tests | CI | Yes | CanonicalIntent migration regressions |
| Temporal selector correctness | Z3 (4 proofs) | proofs/z3_temporal_selector.py | Manual | No | Expired version returned, wrong rail |
| Occupancy state machine | Z3 (6 proofs) | proofs/z3_occupancy.py | Manual | No | Invalid state transitions, tombstone escape |
| Evidence claim precedence | Z3 (4 proofs) | proofs/z3_claim_precedence.py | Manual | No | Antisymmetry/transitivity violation |
| Temporal overlay semantics | TLA+ TLC (12 invariants) | proofs/tla/LawVMTemporalOverlay.tla | Manual | No | Overlapping permanents, expiry chain, ancestor masking |
| Tree ops persistent structure | icontract @ensure | tree_ops.replace_at | Runtime | Yes (crash) | Mutation of shared tree |
| Version effective bound | icontract @ensure | timeline.select_active_version | Runtime | Yes (crash) | Future version returned |
| Node kind non-empty | __post_init__ | IRNode, LegalAddress | Construction | Yes (ValueError) | Empty-kind nodes in tree |
| Barrier kind non-empty | __post_init__ | StrictBarrier | Construction | Yes (ValueError) | Unnamed strict barriers |
| FindingSpec well-formed | __post_init__ | FindingSpec | Construction | Yes (ValueError) | Registry corruption |
| ProvisionVersion dates valid | __post_init__ | ProvisionVersion | Construction | Yes (ValueError) | Temporary without expiry, expiry before effective |
| StrictProfile named | __post_init__ | StrictProfile | Construction | Yes (ValueError) | Anonymous profile |
| Tree ops under random mutation | Hypothesis PBT (23 tests) | tests/test_kernel_properties.py | CI | Yes | find/replace/insert corner cases |
| Sparse item preservation / omission safety | Hypothesis PBT (2 tests) | tests/test_apply_properties.py | CI | Yes | Sparse item merge corruption, undeclared deletion under omission |
| Sparse slot monotonicity | Hypothesis PBT | tests/test_payload_normalize_properties.py | CI | Yes | Payload slot order drift, slot reuse across moments |
| Sparse ambiguity degrades | Hypothesis PBT | tests/test_payload_normalize_properties.py | CI | Yes | Silent guessing on ambiguous sparse slot bindings |
| Sparse coverage partition | Hypothesis PBT | tests/test_payload_normalize_properties.py | CI | Yes | Silent payload slot drops or double-claiming |
| Tree/timeline/phase state machines | Hypothesis stateful (3 machines) | tests/test_stateful_properties.py | CI | Yes | Multi-step sequence bugs |
| Temporary version expiry inheritance | Hypothesis PBT | tests/test_timeline_properties.py | CI | Yes | Temporary content becoming permanent by accident |
| Disjoint temporal stability | Hypothesis PBT | tests/test_timeline_properties.py | CI | Yes | Unrelated versions perturb active selection for another address |
| Renumber lineage determinism | Hypothesis PBT | tests/test_timeline_properties.py | CI | Yes | Renumber chain resolution changes with event order |
| Replay metamorphic seam | Hypothesis PBT (3 tests) | tests/test_replay_metamorphic.py | CI | Yes | Disjoint-op commutativity, locality, failed-op no-op regressions |
| Small-model completeness | Exhaustive enumeration (26 tests) | tests/test_exhaustive_enumeration.py | CI | Yes | Missing edge cases in bounded spaces |
| Code path coverage (SMT) | CrossHair (10 tests) | tests/test_crosshair_kernels.py | Nightly (--run-slow) | No | Unreachable branches, path-dependent bugs |
| Evidence rule isolation | Unit tests (91 in test_proof_algebra) | tests/test_proof_algebra.py | CI | Yes | Rule interaction bugs, parity with legacy |
| Section claim parity | Dual-run (30 parity tests) | tests/test_proof_algebra.py | CI | Yes | A1 typed path diverges from legacy |
| Slot binding admissibility | C2 certificates + tests | tests/test_admissible_binding.py | CI | Yes | Ambiguous bindings undetected |
| Section-local strict lineage | C1 tests (12) | tests/test_section_strict_lineage.py | CI | Yes | Statute-wide barriers misattributed |
| Invariant → evidence | C3 tests (5) | tests/test_section_invariant_evidence.py | CI | Yes | Timeline violations ignored by evidence |
| Publication guarantees | D1-D3 tests (5) | tests/test_publication_guarantees.py | CI | Yes | PROVED claim without rule_id or section scope |
| Worker pool cleanup | Signal handling tests (5) | tests/test_worker_pool.py | CI | Yes | Zombie worker processes |

## What is NOT verified

- No formal refinement map from TLA+ spec to Python implementation
- No seeded-bug injection (mutmut) evaluation of evidence false-negative rate
- No independent external audit of evidence claims
- No section-chain completeness certificate as precondition for negative proofs
- No formal proof that 17 apply failures don't affect section-level PIT output
- No abstract formal semantics of the Finnish amendment language

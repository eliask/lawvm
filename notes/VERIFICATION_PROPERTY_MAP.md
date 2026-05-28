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
| UK replay duplicate/order invariants | UKReplayInvariantDiagnosticsMixin | src/lawvm/uk_legislation/replay_invariant_diagnostics.py | UK replay after structural mutations | No, records adjudications | Duplicate siblings, out-of-order siblings, payload shape gaps |
| UK replay invariant scan equivalence | pytest | tests/test_uk_schedule_compile.py | CI | Yes | Fast UK invariant scanner diverging from persisted generic subset |
| Replay text-duplication lints | replay_lints + frontend projection | src/lawvm/core/replay_lints.py; src/lawvm/uk_legislation/replay_records.py | FI/EU/UK replay fold | No, observation | Large duplicate text tracts after replay |
| Flattened sublist-family lints | replay_lints + diagnose-phase | src/lawvm/core/replay_lints.py; src/lawvm/tools/diagnose_phase.py | Manual/debug | No, observation | Nested list accidentally flattened to one sibling list |
| Shared mutation-boundary report | mutation_boundary | src/lawvm/core/mutation_boundary.py | Open Law audit; reusable by frontends | No, primitive | Snapshot/op changed outside declared target/address region |
| Phase-local invariant attribution | diagnose-phase | src/lawvm/tools/diagnose_phase.py | Manual/debug | No | Whether a structural violation arose before apply, in apply, fold post-process, or materialization |
| First-bad invariant bisection | invariant-bisect | src/lawvm/tools/invariant_bisect.py | Manual/debug | No | First amendment introducing duplicate/order/illegal-edge/text-dup/flattened-list symptoms |
| Apply mutation-boundary accounting | Finland apply_events | src/lawvm/finland/apply_events.py | Finland replay | No, finding/evidence | Successful/failed op mutated outside declared target boundary |
| Publication operation boundary accounting | Open Law audit | src/lawvm/open_law/audit.py | Open Law audit | No, finding | Published snapshot changed paths outside declared operation target regions |

## What is NOT verified

- No formal refinement map from TLA+ spec to Python implementation
- No seeded-bug injection (mutmut) evaluation of evidence false-negative rate
- No independent external audit of evidence claims
- No section-chain completeness certificate as precondition for negative proofs
- No formal proof that 17 apply failures don't affect section-level PIT output
- No abstract formal semantics of the Finnish amendment language
- No UK-wide mutation-boundary checker equivalent to Finland `ApplyMutationEvent`
- No generic changed-path accounting in the UK replay executor; UK currently relies on target-specific adjudications plus duplicate/order invariant scans

## Reuse Before Ad Hoc Detection

When a new frontend symptom is visible as one of these generic properties, prefer the generic detector/evidence lane before adding a bespoke source-family check.

Good candidates:

- duplicate siblings or wrong local ordering: use `check_invariants` or the UK replay invariant adjudication lane
- large repeated wording after replay: use `build_text_duplication_findings`
- suspicious flat repeated list families: use `build_flattened_sublist_findings` / `diagnose-phase --detector flattened_sublist_family`
- unexplained sibling/parent mutation: use `tree_path_from_legal_address` + `build_mutation_boundary_report` before adding more late replay special cases
- phase attribution for Finland-style chains: run `invariant-bisect` then `diagnose-phase --certificate`

UK-specific next opportunities:

- Add UK replay mutation events with `changed_paths`, `covered_changed_paths`, and `unexplained_changed_paths`, using the same conceptual boundary as Finland and Open Law.
- Expose a UK-compatible invariant debug command or extend existing UK bench/candidate output with first new tree-invariant adjudication samples, because `diagnose-phase` / `invariant-bisect` are currently Finland-chain oriented.
- Keep target-family repairs source-owned, but let generic invariants prove symptoms; a source parser should not duplicate a generic duplicate-label or out-of-boundary detector just to recognize the same failure shape.

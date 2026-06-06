# LawVM Proof Surfaces

Status: normative, implementation-near.
Purpose: define the shared report/read-model grammar that prevents evidence,
candidates, diagnostics, manual claims, and oracle comparisons from becoming
replay authority without an explicit proof boundary.

LawVM has two products:

```text
legal text-state
audit / proof account
```

The proof-surface layer is the typed public face of the second product. It
answers what LawVM compiled, what it refused to compile, why, under which
profile, from which source witnesses, and which residuals remain.

It is not an executor.

---

## 1. Core Rule

No evidence row, diagnostic row, candidate row, manual-claim row, source
pathology row, score row, or oracle-comparison row may authorize replay merely
because it exists.

Replay authority requires an explicit execution authorization that says:

```text
executable=true
replay_authorized=true
```

Every other row is evidence, work, proof, residual, or presentation.

The safe default is:

```text
record the uncertainty;
do not mutate legal state;
do not widen targets;
do not delete state to match an oracle;
do not treat source pathology as source truth.
```

---

## 2. Shared Object Grammar

The proof-surface grammar is:

```text
SourceWitness
-> Claim / Assertion
-> ExecutionAuthorization
-> Proof
-> Materialization
-> Agreement
-> Residual / FrontierWorkItem
```

The current core implementation centers are:

```text
src/lawvm/core/source_witness.py
src/lawvm/core/evidence_surface_report.py
src/lawvm/core/proof_surfaces.py
src/lawvm/core/execution_authorization.py
src/lawvm/core/frontier_work_item.py
src/lawvm/core/candidate_set_certificate.py
src/lawvm/core/mutation_boundary_proof.py
src/lawvm/core/agreement_residual.py
src/lawvm/core/provenance_graph.py
src/lawvm/core/evidence_policy.py
src/lawvm/core/evidence_kernel.py
src/lawvm/core/source_acquisition.py
```

Frontend surfaces should reuse these objects before creating local report
shapes.

---

## 3. EvidenceSurfaceReport

`EvidenceSurfaceReport` is the report envelope. It declares what a report
claims and does not claim.

Required claim bits:

```text
replay_claims
canonical_effect_claims
candidate_effect_claims
dry_run_claims
agreement_claims
```

Rules:

- If `replay_claims=false`, rows in the report are not replay authority.
- If `candidate_effect_claims=true`, candidates still need execution
  authorization before replay.
- If `agreement_claims=false`, comparison rows are diagnostics, not oracle
  adjudications.
- `truth_claim` must describe the narrow truth surface, not a broad marketing
  claim.
- `detail.forbidden_shortcuts` should name the shortcuts the report explicitly
  rejects.

Example forbidden shortcuts:

```text
frontier_item_as_canonical_operation
candidate_certificate_as_slot_uniqueness_proof
source_witness_as_replay_authorization
agreement_residual_as_mutation_instruction
mutation_boundary_proof_as_replay_authorization
```

---

## 4. ProofSurface

`ProofSurface` is a queryable read model over report rows. It does not replace
`EvidenceSurfaceReport`; it projects report rows into a stable relation.

It should be used when downstream tooling needs to query rows by:

```text
row_id
subject_id
row_kind
status
source_refs
witness_refs
assertion_refs
proof_refs
authorization_ref
residual_refs
frontier_ref
```

Rules:

- A proof surface is a read model only.
- A proof surface may preserve legacy report JSON inside `detail`.
- Missing source/proof/frontier refs should stay missing, not be invented.
- Stable row ids are for audit/query identity, not legal identity.

---

## 5. ExecutionAuthorization

`ExecutionAuthorization` is the waist between "this row exists" and "this row
may mutate legal state."

It must state:

```text
executable
replay_authorized
authorization_status
authorization_rule_id
owner_phase
strict_disposition
quirks_disposition
required_proofs
safe_default
forbidden_shortcuts
```

Rules:

- `replay_authorized=true` requires `executable=true`.
- A non-authorized row must list required proofs.
- Satisfied evidence policy is not automatically replay authority.
- Candidate-only, diagnostic-only, source-pathology, recovery, temporal, and
  agreement rows should normally be non-executable.
- Recovery rows need mutation-boundary proof before promotion.

The EvidenceKernel adapter follows the same rule: a satisfied declarative
evidence policy remains non-replay-authorizing unless the caller explicitly
sets the phase-local replay gate.

---

## 6. FrontierWorkItem

A `FrontierWorkItem` is a first-class product, not a failure.

It should be emitted when LawVM has a bounded non-executable item:

```text
manual claim needed
source pathology needs review
payload slot ambiguity remains
source acquisition gap remains
oracle/editorial adjudication remains
temporal or applicability proof is missing
```

Rules:

- The owner phase must be explicit.
- Missing proofs must be explicit.
- Source witnesses should be attached when a bounded source preview or digest
  exists.
- Frontier rows must not be silently dropped from strict reports.

---

## 7. CandidateSetCertificate

A `CandidateSetCertificate` describes the candidate set behind a selection or
blocked selection.

It is required when a row would otherwise smuggle an implicit uniqueness claim.

Rules:

- A selected candidate is not enough; enumerate whether the set is complete.
- If the set is partial, say why.
- If uniqueness is unproved, block promotion or require proofs.
- Do not use candidate order as legal priority unless a named rule owns it.

Finland sparse-slot diagnostics and UK candidate/frontier rows are the current
reference consumers.

---

## 8. SourceWitness and DigestWitness

`SourceWitness` identifies a bounded source surface. `DigestWitness` proves byte
or preview identity.

Use them for:

```text
source XML
consolidated XML
official PDFs
effect-feed rows
current-page snapshots
manual-claim source snippets
source-pathology previews
```

Rules:

- Prefer artifact digest plus preview digest when bytes are available.
- Preview-only witnesses are weaker and must stay weaker.
- Locator identity, embedded version identity, and source lane should remain
  distinct.
- Source availability counts are not source identity proof.
- Date availability counts are not commencement proof.

---

## 9. SourceBundlePolicy

`SourceBundlePolicy` separates source-set admission from replay recovery.

Use it for source substrates such as:

```text
official XML
official PDFs
pre-AKN source material
OCR text
manual source atoms
corrigendum source packets
```

Rules:

- Admission to a source bundle is not replay authorization.
- Source-acquisition attestations are not semantic compilation.
- PDF/OCR/pre-AKN source lanes should require explicit attestations before
  admission.
- A blocked source lane should become source-acquisition frontier work, not a
  hidden fallback to another source.
- Even an admitted source bundle still needs phase-local authorization before
  legal-state mutation.

---

## 10. MutationBoundaryProof

`MutationBoundaryProof` is passive proof that an operation's changed paths stay
within the authorized mutation region.

Rules:

- It does not authorize replay by itself.
- Unexplained changed paths remain residuals or blockers.
- Declared recovery paths must cite recovery rule ids.
- Declared migration paths must cite migration rule ids.
- It is valid to report mutation-boundary failure; do not hide it to improve a
  score.

---

## 11. AgreementResidual

An `AgreementResidual` classifies disagreement between named surfaces.

Examples:

```text
replay legal PIT vs official consolidation
source XML vs current HTML
current text vs source omission
Finlex inline repeal stub vs replay timeline
publication DB row vs verified manual review
```

Rules:

- The agreement surface must be named.
- A residual is not a mutation instruction.
- Oracle text is not source truth unless the jurisdiction makes it so.
- Agreement classifications should separate replay bugs, source pathologies,
  oracle/editorial pathologies, manual frontiers, and non-commensurable views.

---

## 12. Finland Reference Surface

Finland is the reference compiler frontend. Its proof surfaces should expose
the clean compiler chain without hiding recoveries:

```text
token tape / surface parse
payload elaboration
sparse slot candidate certificates
source pathology frontiers
temporal resolution evidence
source completeness status
mutation boundary proof rows
recovery execution authorizations
government proposal branch diagnostics
Finlex agreement residuals
corrigendum source/manual/frontier rows
bench evidence surfaces
```

Rules:

- ClauseAST remains the semantic authority; compatibility parsed ops are
  derived artifacts.
- Payload elaboration owns live-state-dependent recovery; replay must not
  rediscover meaning.
- Strict/quirks recovery rows should be visible as non-executable authorization
  projections unless a phase-local gate proves execution authority.
- Government proposal branch rows are future-law diagnostics. They are not
  enacted-law authority, canonical operations, candidate effects, dry-run
  authority, or current-law agreement claims.
- Finlex comparison is an agreement surface, not the compilation objective.

---

## 13. Adoption Rule

Before adding a new local status row, report row, workqueue row, candidate row,
or diagnostic envelope, check whether it is one of:

```text
EvidenceSurfaceReport
ProofSurfaceRow
ExecutionAuthorization
FrontierWorkItem
CandidateSetCertificate
SourceWitness / DigestWitness
MutationBoundaryProof
AgreementResidual
TemporalResolutionEvidence
SourceBundlePolicy / SourceAcquisitionAssertion
```

If none fit, add the new local row only with a narrow TODO or spec note
explaining the missing abstraction.

Do not change replay semantics merely to satisfy a proof-surface abstraction.

---

## 14. Completion Rule

A frontend is not complete because a score is high.

A frontend is complete for a declared slice only when every row in the slice is
classified as one of:

```text
executable replay with authorization
candidate-only and blocked from replay
typed non-executable frontier work
source pathology
oracle/editorial pathology
manual-claim frontier
agreement residual
non-commensurable surface
out-of-scope source acquisition gap
```

Completion is accounting plus evidence, not silence.

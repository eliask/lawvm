> **Status (2026-06-22):** Largely-current, some stale refs. Kind: Normative. CandidateSetCertificate was renamed to CandidateSetCoverage (file `candidate_set_certificate.py` -> `candidate_set_coverage.py`); update §2 file list (line 72) and §7 type name. All other 19 core-file citations verified live.

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
src/lawvm/core/candidate_set_coverage.py
src/lawvm/core/mutation_boundary_proof.py
src/lawvm/core/agreement_residual.py
src/lawvm/core/provenance_graph.py
src/lawvm/core/proof_surface_graph.py
src/lawvm/core/evidence_policy.py
src/lawvm/core/evidence_kernel.py
src/lawvm/core/source_acquisition.py
src/lawvm/core/source_completeness.py
src/lawvm/core/source_pathology.py
src/lawvm/core/frontend_contract.py
src/lawvm/core/frontend_phase_surface.py
src/lawvm/core/token_tape.py
src/lawvm/core/payload_elaboration.py
src/lawvm/core/verification_contracts.py
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
- `claim_flags` preserve the originating report's claim boundary; they do not
  authorize any proof-surface row by themselves.
- A proof surface may preserve legacy report JSON inside `detail`.
- Missing source/proof/frontier refs should stay missing, not be invented.
- Stable row ids are for audit/query identity, not legal identity.
- Graph projection of proof-surface rows is observation-only. It may create
  provenance graph nodes and `derives_projection` edges, but it does not verify
  source spans and does not authorize replay.
- Derived compatibility artifacts, such as Finland `ParsedOp` projections from
  `ClauseAST`, should declare their source artifact, lossy boundary, and
  non-authority status instead of relying on prose.
- Frontend diagnostics should project to governed `Finding` rows when they
  cross a phase boundary. Human diagnostic strings may remain as rendering
  compatibility, but they should not be the only control-plane record.
- Token tapes should preserve the immutable source lexeme stream. Parser-facing
  structural views should be derived from annotation overlays and keep a
  view-to-raw token-span map.
- Payload elaboration reports should expose completeness, slot bindings,
  rejected-operation counts, and source-pathology counts through shared
  evidence-report rows. Those rows are not replay authority and do not prove
  target uniqueness or mutation-boundary safety by themselves.
- Payload completeness witnesses should be first-class rows when present. A
  completeness witness can classify ownership and tail policy, but it is not a
  mutation-boundary proof and does not authorize replay by itself.
- Source pathologies should project as passive rows with affected phase,
  suggested lane, blocking status, and forbidden shortcuts. A source-pathology
  row records why source material cannot be used literally; it is not source
  truth, replay authorization, target widening, or mutation-boundary proof.
- Source-completeness rows should record source-chain and date coverage as
  passive diagnostics. A complete source/date count is not source identity
  proof, commencement proof, or replay authorization.
- Frontend capability declarations should project as passive rows listing the
  phase waists a frontend claims to expose. A capability row is not parse
  success, replay authority, or canonical-effect proof.
- Frontend capability matrix reports should aggregate multiple declarations as
  passive status rows. They are for phase-contract visibility, not for
  selecting targets, admitting candidates, or authorizing replay.

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

Graph-native manual-claim authorization uses the same adapter path. A manual
claim assertion satisfying an evidence policy is report-visible, but it remains
non-executable and non-replay-authorized unless a separate phase-local replay
gate is supplied.

Legacy manual-claim composition has a StrictProfile-authoritative bridge. The
v2.2 `ClaimCompositionDecision` still carries a deprecated compatibility
profile label, but new composer callers should derive that label from
StrictProfile attested-channel policy rather than selecting profile authority
from manual-claim data.

Execution-authorization evidence reports may carry explicit authorization rows.
The report envelope's `replay_claims` bit must be derived from row-level
`replay_authorized=true`; evidence-policy success alone must still project as a
non-authorizing row with required proofs.

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
- Frontend-local workqueue rows, such as Finland open-manual corrigendum
  candidates, should project to `FrontierWorkItem` rows when they cross a report
  boundary. They remain non-executable triage until the required claim and
  mutation-boundary proofs exist.

---

## 7. CandidateSetCoverage

A `CandidateSetCoverage` describes the candidate set behind a selection or
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

- Source locators should use the shared core locator/read model when source
  footing crosses report, graph, or manual-claim boundaries.
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
- Source-bundle admission reports should expose `SourceAcquisitionAssertion`
  and `SourceBundleAdmission` rows with non-executable authorization metadata.
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
An `AgreementSurface` is the report/read-model envelope over those residuals.

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
- The agreement surface should name both materialization kinds: the replay side
  might be `legal_text_state`, while the compared witness might be
  `official_consolidation_view`, `editorial_display_view`,
  `proposed_future_branch`, or `source_as_enacted`.
- A residual is not a mutation instruction.
- An agreement-surface report may set `agreement_claims=true`, but that means
  only "this report is a comparison/adjudication surface"; it is not replay
  authority.
- Oracle text is not source truth unless the jurisdiction makes it so.
- Agreement classifications should separate replay bugs, source pathologies,
  oracle/editorial pathologies, manual frontiers, and non-commensurable views.

---

## 12. CurrentTextVerificationMatrix

`CurrentTextVerificationMatrix` is the reusable A-G gate for source-backed
current-text review packets.

The gates are:

```text
current_body_text_contains_target_phrase
current_status_page_check
source_explicitly_omits_or_repeals_same_text
commencement_in_force
same_territorial_extent
no_later_reinsertion_revival_or_replacement_found
target_phrase_in_operative_text_not_commentary
```

Rules:

- The matrix is a verification/reporting object, not replay authority.
- Public-facing candidates should pass every gate before being treated as
  email-safe; `commencement_in_force=not_applicable` is acceptable.
- `requires_public_html_review`, `unknown`, and `no` remain blockers.
- Passing the matrix still means "source/current-text contrast worth review",
  not a final legal conclusion.

---

## 13. Finland Reference Surface

Finland is the reference compiler frontend. Its proof surfaces should expose
the clean compiler chain without hiding recoveries:

```text
token tape / surface parse
frontend phase-surface report rows
payload elaboration
sparse slot candidate certificates
source pathology frontiers
temporal resolution evidence
source completeness status
source-bundle admission rows
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
- Government proposal branch impact rows should project through the shared core
  branch graph/projection vocabulary when possible. A branch impact projection
  describes proposal-local effects only; it is not enacted-law materialization.
- Finlex comparison is an agreement surface, not the compilation objective.
- Corrigendum source manifests and corpus overviews should expose PDF/date
  coverage through passive `SourceCompletenessStatus` rows while preserving
  compatibility summaries. Source/date coverage does not prove source identity,
  commencement, source-text repair, or replay authority.
- Open manual corrigendum listings should emit shared `FrontierWorkItem` rows
  alongside compatibility rows. A listed candidate is not a manual claim and is
  not replay-authorized.

---

## 14. Adoption Rule

Before adding a new local status row, report row, workqueue row, candidate row,
or diagnostic envelope, check whether it is one of:

```text
EvidenceSurfaceReport
ProofSurfaceRow
ExecutionAuthorization
FrontierWorkItem
CandidateSetCoverage
SourceWitness / DigestWitness
MutationBoundaryProof
AgreementResidual
CurrentTextVerificationMatrix
TemporalResolutionEvidence
SourceBundlePolicy / SourceAcquisitionAssertion
FrontendPhaseSurface
FrontendCapability
```

If none fit, add the new local row only with a narrow TODO or spec note
explaining the missing abstraction.

Do not change replay semantics merely to satisfy a proof-surface abstraction.

---

## 15. Completion Rule

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

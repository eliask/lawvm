# Source Pathology And Adjudication Spec

Status: living spec, intentionally partial.
Kind: normative.

Purpose:

- define when LawVM should surface source pathology or adjudication instead of
  silently recovering
- keep source faults distinct from replay faults and oracle faults

Related docs:

- [FINLAND_ELABORATION_RULES.md](FINLAND_ELABORATION_RULES.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)
- [REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md)

## 1. Core Rule

When the source artifact does not justify a unique semantic recovery, the
compiler should prefer:

- explicit pathology
- explicit adjudication
- explicit unresolved evidence

over:

- silent replay certainty

## 2. Pathology vs Adjudication

### 2.1 Source pathology

Source pathology is for defects or underdetermination in the published source
artifact itself.

Examples:

- payload shape loss
- missing body support for a blamed replacement
- malformed broad replace body
- container payload overbundling standalone sections
- sparse item body absent from published payload
- partial base source missing whole chapter spans while only leaving textual
  sentinels such as `Puuttuu luvut 7-11`

### 2.2 Adjudication

Adjudication is a typed compiler-side decision or warning about how a recovery
was performed or why it could not be performed cleanly.

Examples:

- duplicate-text warning
- unsupported target pattern
- clause/frontend fallback
- unresolved live-state ambiguity

Pathologies are about the source.
Adjudications are about the compiler's reasoning surface.

## 3. Observation And Claim Layers

The evidence layer should not collapse raw observations, candidate
explanations, and outward claims into one step.

It should proceed as:

1. collect section observations
2. build candidate section claims
3. resolve them into one section conclusion
4. aggregate those into statute-level outward claims

### 3.1 `SectionObservation`

A `SectionObservation` is an atomic fact emitted or derived from one phase.

It should carry:

- section key
- source phase
- field/value
- scope

At minimum, scope should distinguish:

- `SECTION_STATE`
- `BLAME_STEP`
- `STATUTE_SCOPE`

### 3.2 `SectionClaim`

A `SectionClaim` is a candidate explanation layer, not yet the final outward
statute classification.

It should carry:

- outward tier compatibility
- claim kind
- scope
- polarity
- strength
- disposition

Important distinctions:

- `SECTION_STATE` vs `BLAME_STEP`
- `POSITIVE_EXPLANATION` vs `REPLAY_DEMOTION`
- `DIRECT_PROOF` vs `RISK_SIGNAL`

That is how the model avoids over-promoting "this defeats replay attribution"
into "this fully proves source pathology."

Current migration state:

- evidence bundles now emit a first `section_claims` surface
- this is intentionally partial and conservative
- it is the first explicit section-first candidate explanation layer between
  raw compiler/source observations and outward statute-level proof tiers
- review artifacts can now aggregate selected `section_claims` kinds across
  bundles, so corpus triage is no longer limited to statute-level proof kinds
- frontier proof rows and proof summaries now also aggregate
  `section_claim_kinds`, so the higher-level replay work queue can see
  section-first explanation families directly
- `section_claims` now also record `defeated_candidate_kinds` plus basic
  `defeated_candidates` metadata:
  - losing kind
  - losing inference rule
  - winning kind
  - winning inference rule
  - winning observation-source families
- review / frontier summaries now aggregate both defeated kinds and defeated
  inference-rule families, not only the winning selected claim kinds

## 4. Trigger Observations, Corroborators, Blockers, Competitors

### 4.1 Trigger observations

Triggers are the minimal fact set that makes an inference rule fire.

### 4.2 Corroborators

Corroborators support or strengthen a claim but are not sufficient alone.

### 4.3 Blockers

Blockers prevent a narrower proof from being promoted to a broader one.

Example:

- a blame-step payload-preference fact may block replay attribution
- but `preexisting_before_any_drop` can block promotion to a full
  section-state source-pathology conclusion

### 4.4 Defeated competitors

Sections almost always have competing explanations.

The evidence layer should keep explicit records of which candidate claims were
defeated and why.

Current migration state:

- section-level evidence now records defeated alternative claim kinds
- current code does not yet record defeat reasons separately
- the next refinement is to attach defeat reasons / defeating observations, not
  just the defeated kind names
- section-level replay demotion now also includes a conservative
  preexisting-residue rule for cases where:
  - the section is already materially divergent before the blamed amendment
  - and the blamed amendment only causes a negligible score drop
  This should remain `UNRESOLVED`, not `PROVED_SOURCE_PATHOLOGY`, unless a
  stronger positive source/oracle explanation is also present.

### 4.5 Frontend observation bridge

Before the full observation/claim model exists, frontend/elaboration
observations may surface as generic compile adjudications.

Current Finland bridge:

- `normalize_group_payload(...)` emits typed elaboration observations
- replay metadata preserves them as `elaboration_observations`
- `compile_fi(...)` exposes them as
  `frontend_elaboration_observation`

These are not outward proof claims by themselves.

They are a transport layer that keeps frontend reasoning visible until the
evidence stack consumes a richer structured observation stream directly.

Current first evidence use:

- same-section frontend sparse elaboration observations may block promotion of
  a residual section to `PROVED_REPLAY_BUG`
- the resulting claim should remain `UNRESOLVED`
- this is a blame-step ambiguity blocker, not direct source-pathology proof

## 5. Direct Pathologies vs Risk Signals

Not every bad-looking source shape should directly prove source blame.

## 5.0 Adjudication lanes

The compiler emits observations into distinct lanes:

- **source pathology**: defects in the published source artifact itself
- **replay demotion**: facts that defeat replay attribution without proving
  source pathology (e.g., preexisting divergence, payload preference)
- **risk signal**: suspicion indicators that should not by themselves prove cause
- **editorial**: Finlex or other editorial metadata encoded in consolidated
  artifacts (e.g., inline repeal stubs); distinct from source and replay lanes

Each family declares its lane and scope explicitly.

### 5.1 Direct pathologies

These can directly support a positive source-pathology claim when they align
with the disputed section/amendment:

- `PARTIAL_WHOLE_SECTION_PAYLOAD`
- `MALFORMED_BROAD_REPLACE_BODY`
- `CONTAINER_MEMBERSHIP_MISMATCH`
- `SPARSE_ITEM_BODY_MISSING`
- `PAYLOAD_PREFERS_REPLAY`
- `BASE_MISSING_CHAPTER_SPAN`

### 5.2 Replay demotion facts

These often defeat replay attribution without yet proving a full section-state
source-pathology conclusion:

- `preexisting_before_any_drop`
- materially preexisting divergence plus only negligible blamed-step drop
- `preexisting_same_section_structure_drift`
- `blamed_amendment_improves_section`
- `blamed_source_lacks_payload_support`
- `blamed_source_payload_prefers_replay`
- `same_chapter_oracle_range_drift`

These should carry explicit `BLAME_STEP` or `SECTION_STATE` scope rather than
being flattened immediately into one outward tier.

### 5.3 Risk signals

These indicate increased suspicion, but should not by themselves prove cause:

- `DESTRUCTIVE_SHAPE_LOSS_RISK`

Risk signals should usually support:

- `SUPPORTED`
- or `UNRESOLVED`

not immediate proved source-pathology output by themselves.

## 6. Source Pathology Evidence Classes

Every source-pathology family should declare whether it acts as:

- `DIRECT_SOURCE_PROOF`
- `REPLAY_DEMOTION_ONLY`
- `RISK_SIGNAL`

and at what scope:

- `SECTION_STATE`
- `BLAME_STEP`
- `STATUTE_SCOPE`

## 7. Required Emission Situations

The compiler should emit pathology or adjudication when:

- payload structure is too degraded for a unique mapping
- multiple live targets remain equally plausible
- a blamed amendment compiles as a replacement but the published body does not
  support that replacement
- a container payload bundles child content that is also emitted as standalone
  canonical targets
- a base source drops a whole chapter span and leaves only a textual gap
  sentinel in the preceding section tail
- replay would otherwise need to guess semantics after canonicalization

## 8. Current Important Pathology Families

### 8.1 Base missing chapter span

Meaning:

- the base source jumps over a whole chapter range and leaves only textual
  sentinel content such as `Puuttuu luvut 7-11`

Required response:

- classify the gap explicitly as source/base incompleteness
- allow chapter seeding to consume the sentinel when later source material
  provides a real chapter wrapper
- do not misclassify downstream section failures in that span as pure frontend
  sparse-slot bugs first

### 8.2 Container membership mismatch

Meaning:

- a container payload bundled standalone sections that do not belong to the
  canonical container membership for that grouped op

Required response:

- prune the bundled children
- preserve the intended container-level op
- emit `CONTAINER_MEMBERSHIP_MISMATCH`
- preserve `pruned_sections`

### 5.2 Sparse item body missing

Meaning:

- a sparse payload claims an item-level change in johto/canonical targeting,
  but the published body does not actually reproduce the item body

Required response:

- drop only the unsupported narrow item replace
- keep the rest of the sparse payload family if justified
- emit `SPARSE_ITEM_BODY_MISSING`

### 5.3 Payload prefers replay

Meaning:

- the published section payload materially matches replay output better than the
  consolidated oracle output

Required response:

- evidence should be allowed to demote replay blame toward source-pathology or
  oracle incorrectness depending on the rest of the section context

### 8.3 Base source ontology defects (Finland)

#### 8.3.1 Base unnumbered paragraph peer

Meaning:

- a `<paragraph>` in base source XML has no `<num>` element and sits as a
  sibling of numbered `<paragraph>` elements under a `<subsection>`
- under the Finnish legal ontology (Lainkirjoittajan opas), no legitimate law
  point has this shape; the XML has flattened a continuation belonging nested
  under the preceding numbered kohta

Examples:

- 2013/331 § 3 / 1 mom.: unnumbered peer containing the exclusion clause
  (`kaatopaikkana ei kuitenkaan pidetä:`) (discovered 2026-04-15; see
  `2013_331_UNNUMBERED_PEER_CASE_STUDY.md`)

Required response:

- emit observation carrying `(eId, parent_path, position_in_siblings)` without
  rewriting the tree initially
- future T4 will reparent under preceding numbered sibling
- this defect chains into oracle duplicate-child-label (§8.4.1) and diff
  alignment artifacts

Classification: `DIRECT_SOURCE_PROOF`, `SECTION_STATE`.
Discovery: source parse phase (`fi_xml_to_ir_node` + post-passes); T1 commit
d069d91a silently dropped these.

#### 8.3.2 Label-eId divergence

Meaning:

- a paragraph child of a subsection has `label='N'` but its eId does not end
  with `...para_N`
- the label and eId have drifted apart; signals content loss during parse
  that shifted eId numbering out of alignment with explicit labels

Examples:

- 2013/331 § 3 / 1 mom.: paragraph with `label='2'` but `eId='...para_3'`
  because unnumbered `para_2` was dropped during parse (case study §3.1)

Required response:

- emit observation on the paragraph node carrying `(label, eId)`
- use as a downstream risk signal for detecting hidden content loss
- cheap check to flag label-eId misalignment on day one

Classification: `RISK_SIGNAL`, `SECTION_STATE`.
Detection: compare `parse_label` vs. trailing integer of eId immediately after
`fi_xml_to_ir_node` completes.


#### 8.3.4 Base unnumbered paragraph peer duplicate-child label

(See §8.4.1 for the oracle-side projection collision.)

#### 8.3.5 Base encoding ontology mismatch (reserved for future work)

Meaning:

- XML contains a structural shape with no legitimate Finnish legal-unit mapping
  that the normalization rules can resolve
- umbrella category for newly discovered ontology violations pending specific
  rule implementation

Required response:

- emit observation carrying the unrecognized pattern
- classify as `RISK_SIGNAL` until a specific normalization rule is added
- promote to `DIRECT_SOURCE_PROOF` once the rule's correction is formalized

Classification: `RISK_SIGNAL`, `SECTION_STATE`.
Status: reserved for future wave-1+ discoveries not yet classified.

**Note:** `BASE_SYNTHETIC_EID_RENAME` was renamed to `ORACLE_SYNTHETIC_EID_RENAME` and moved to §8.5 per `notes/FINLAND_INTRA_KOHTA_DECISION_CORRIGENDUM_2026-04-15.md` §1.3.

### 8.4 Semantic projection defects

#### 8.4.1 Oracle duplicate child label / Replay duplicate child label

Meaning:

- `semantic_structure_from_oracle` / `semantic_structure_from_ir` would have
  assigned two siblings the same `label` before the two-pass labeling fix
- happens when an unlabeled child gets an ordinal-fallback label that
  collides with a sibling's explicit label
- observable when source or oracle has unnumbered peers (§8.3.1) or other
  unlabeled sibling encoding

Examples:

- 2013/331 § 3 / 1 mom., oracle @20180781: unnumbered `para_2` and explicit
  `para_2_2` both assigned label `'2'` (case study §3.2); T2 fix applies
  two-pass collision avoidance

Required response:

- already handled by `_next_free_ordinal` in `semantic/projection.py`
- when a collision would have happened under naive counting, emit a typed
  observation `oracle_duplicate_child_label` / `replay_duplicate_child_label`
  carrying `(parent_path, colliding_label, assigned_label_fallback)` on the
  parent's `SemanticStructureNode.defects`
- do not fail; the two-pass fix picks the next free ordinal (e.g. `'2a'`)

Classification: `RISK_SIGNAL`, `SECTION_STATE`.
Implementation: T2 commit a9034ce8 averted the collision; observation emission
deferred.

### 8.5 Editorial oracle artifacts

#### 8.5.1 Oracle synthetic eId rename

Meaning:

- the consolidated oracle uses an eId form not produced by the source
  (e.g. `para_2_2` in 2013/331 @20180781)
- Finlex consolidation pipeline rewrote eIds to match visible `<num>` labels
  during the CONSOLIDATION process; only appears in consolidated oracle XML,
  never in base source XML

Examples:

- 2013/331 @20180781: renamed base `para_3` to `para_2_2` to align eId with
  visible label (case study §2.3)

Required response:

- not yet implemented; when detected, emit observation only (no behavior change)
- use as a risk signal for oracle vs. replay comparison
- do not assume eId numbering is monotonic in consolidated artifacts

Classification: `RISK_SIGNAL`, `SECTION_STATE`.
Status: observation-only, detection logic deferred.

#### 8.5.2 Finlex inline repeal stub

Meaning:

- Finlex consolidated XML encodes repealed kohdat using italic paragraph stubs
- recognized by synthetic `eId` matching `.*para_\d+v\d{8}$` and text matching
  `"N kohta on kumottu A:lla DD.MM.YYYY/NNNN"`
- this is editorial metadata embedded by Finlex, not law; not a source pathology
  or replay defect, but a distinct editorial lane in the adjudication vocabulary

Examples:

- 2013/331 @20211030: `para_2v20211030` with text `"2 kohta on kumottu A:lla
  25.11.2021/1030."` (case study §4)

Required response:

- strip from oracle projection before building semantic structure
- emit observation carrying `(eId, target_range, amendment_id, amendment_date)`
- cross-check against `ProvisionTimeline` terminator and produce one of the
  three evidence records below

Classification: **editorial lane** (new first-class lane distinct from source
pathology and replay demotion). Scope: `STATUTE_SCOPE`.
Implementation: T6 commit a9034ce8 strips stubs at projection; T4/Gap 4
implements the cross-check in `src/lawvm/finland/editorial_adjudication.py`.

#### 8.5.3 Editorial witness confirmed

Kind: `editorial_witness_confirmed`

Meaning:

- a Finlex inline repeal stub's claimed `amendment_id` matches the
  `ProvisionTimeline` repeal terminator for the same slot
- the Finlex editorial layer independently corroborates LawVM's compiled
  lineage; this is positive secondary evidence of correct replay

Emitted by: `editorial_adjudication.cross_check_stub_observations`

Fields: `slot_address` (string repr of `LegalAddress`), `amendment_id`

Classification: **editorial lane**, positive evidence. No pathology.

#### 8.5.4 Editorial witness disagrees

Kind: `editorial_witness_disagrees`

Meaning:

- a Finlex inline repeal stub's claimed `amendment_id` does NOT match the
  `ProvisionTimeline` repeal terminator for the same slot
- real disagreement: either the Finlex editorial stub is wrong (editorial
  error) or the LawVM timeline is wrong (replay bug / missing source data)
- requires manual triage

Emitted by: `editorial_adjudication.cross_check_stub_observations`

Fields: `slot_address`, `amendment_id` (stub's claim),
`timeline_terminator` (timeline's terminator id)

Classification: **editorial lane**, real disagreement. Treat as `RISK_SIGNAL`
until triaged; can escalate to `REPLAY_DEMOTION_ONLY` if timeline is wrong or
`DIRECT_SOURCE_PROOF` if stub is wrong.

#### 8.5.5 Editorial witness unresolved

Kind: `editorial_witness_unresolved`

Meaning:

- a Finlex inline repeal stub claims a slot was repealed, but no
  `ProvisionTimeline` terminator (direct or ancestor drill-down) can
  corroborate it
- possible causes: replay bug (repeal op was not emitted), missing source
  data (amendment not in corpus), or stub is a pure editorial convention
  with no corresponding legal operation

Emitted by: `editorial_adjudication.cross_check_stub_observations`

Fields: `slot_address`, `amendment_id` (stub's claim), `timeline_terminator`
(always `None`)

Classification: **editorial lane**, uncertain. Treat as `RISK_SIGNAL` pending
investigation; do not conflate with a source or replay defect without further
evidence.

### 8.6 Oracle metadata defects

#### 8.6.1 Oracle metadata collapsed dates

Meaning:

- multiple cached consolidated artifacts share the same `date_consolidated`
  even though their embedded amendment version tags differ
- Finlex stamps all cached editorial variants with the same "as of" date
  regardless of which amendment version each embeds; breaks strict
  self-comparability checks
- consequences: `structural-review` and `diff` tools disagree on which oracle
  version to use (§3.3 of case study)

Examples:

- 2013/331: all four cached consolidated artifacts (@20150103, @20160960,
  @20180781, @20211030) share `date_consolidated = 2021-11-25` (case study
  §3.3); `bench_comparable` selector rejects the latest because its embedded
  amendment effective date is later than the common consolidation date

Required response:

- compute self-comparability from embedded version's effective date, not from
  `date_consolidated`
- emit observation carrying the metadata collision
- continue to accept the artifact; use the computed effective PIT instead

Classification: `RISK_SIGNAL`, `STATUTE_SCOPE`.
Implementation: T5 commit dd3d631c (Option Z): switch to computing oracle PIT
from embedded version + amendment effective dates; observation emission
deferred.

## 9. Relationship To Evidence

Pathology/adjudication emission does not automatically decide statute-level
proof tier.

Instead:

- compiler phases emit typed pathologies and adjudications
- evidence consumes them together with bisect, trace, and source/oracle support
- statute-level proof is derived afterward

Section-level outward claims should be built after candidate claim resolution,
not by directly mapping every bad-looking fact to a public proof tier.

## 10. Near-Term Implementation Direction

The next desirable direction is:

- a stable compiler observation stream shared across frontend/elaboration/apply
  phases

That stream should let evidence consume:

- what was inferred
- why it was inferred
- what was rejected
- what remained unresolved

without reverse-engineering everything from end-state diffs.

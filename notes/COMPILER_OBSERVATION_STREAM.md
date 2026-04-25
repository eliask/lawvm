# Compiler Observation Stream

Status: living spec, intentionally partial.
Kind: normative.

Purpose:

- define the observation layer between compiler execution and evidence claims
- keep raw compiler facts distinct from later explanations and outward proof
- give LawVM one coherent place to hang frontend and replay-side observation
  streams

Related docs:

- [SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md](SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md)
- [REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md)
- [FINLAND_ELABORATED_GROUP_SPEC.md](FINLAND_ELABORATED_GROUP_SPEC.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)

## 1. Core Rule

The compiler should emit observations before it emits conclusions.

Observations are:

- phase-local
- typed
- attributable
- non-final

They are not yet:

- outward proof tiers
- statute-level diagnoses
- policy decisions about what to hide or summarize

## 2. Why This Layer Exists

LawVM already has multiple useful facts that are too structured to be plain
logs but too early to be final evidence claims.

Current examples:

- Finland frontend/elaboration observations from payload normalization
- Finland sparse slot bindings from subgroup elaboration
- Finland sparse payload leftover-slot records from subgroup elaboration
- apply-time mutation/accounting events from replay execution
- source-pathology facts emitted during compilation/replay

Without a shared observation layer, those facts either:

- disappear
- become ad hoc adjudications too early
- or must be reverse-engineered later from end-state diffs

## 3. Observation Families

### 3.1 Frontend elaboration observations

Meaning:

- facts emitted while resolving clause/payload/live-state interactions before
  canonical replay execution

Current Finland examples:

- sparse omission alignment
- sparse split across consecutive replaces
- sparse item-drop due to missing amendment body
- container payload pruning
- exact duplicate target ops emitted by frontend extraction/supplement merge
- move / renumber target collapse detected before replay apply

Required fields:

- phase
- observation kind
- source statute
- target scope
- structured detail payload

### 3.2 Frontend sparse payload leftovers

Meaning:

- leftover sparse payload slots emitted after subgroup elaboration could not
  assign every local payload slot to a logical changed moment

Current Finland examples:

- unassigned numbered sparse subsection slots
- unassigned unlabeled tail slots

Required fields:

- source statute
- target scope
- unassigned slot labels

### 3.3 Frontend sparse slot bindings

Meaning:

- explicit assigned logical moment -> payload-slot ownership rows emitted after
  subgroup elaboration

These are not replay instructions. They are frontend facts about which payload
slot was judged to belong to which logical changed moment.

Required fields:

- source statute
- target scope
- op-side logical moment identity
- payload slot index / label

### 3.4 Apply mutation events

Meaning:

- facts emitted while replay actually mutates or attempts to mutate the tree

Current examples:

- deterministic subsection replace applied
- whole-section helper used
- placeholder created or consumed
- top-level apply failed with explicit reason

Required fields:

- source statute
- canonical action
- helper
- outcome
- target path / parent path
- touched-path accounting
- fallback tags
- failure reason when relevant

### 3.5 Source-pathology observations

Meaning:

- facts about degraded, incomplete, or noncommensurable source artifacts

These may later support:

- source-pathology adjudications
- replay demotions
- or evidence claims

But the raw observation should still exist independently of the final claim.

## 4. Observation Shape

The cleanroom target is one conceptual schema, even if the implementation uses
different concrete dataclasses during migration.

Minimum shared fields:

- `family`
  - `frontend_elaboration`
  - `frontend_sparse_payload_leftovers`
  - `apply_mutation`
  - `source_pathology`
- `phase`
- `source_statute`
- `scope`
  - section target
  - chapter target
  - statute scope
  - blame-step scope
- `kind`
- `detail`

Optional fields:

- `op_id`
- `resolved_target_path`
- `parent_path`
- `confidence`
- `consumed_paths`
- `created_paths`
- `removed_paths`
- `replaced_paths`

## 5. Relationship To Adjudications

Adjudications are a compatibility/reporting surface during migration.

That means:

- observations may be mirrored into compile adjudications
- but adjudications are not the normative observation schema
- and the evidence layer should eventually consume structured observations
  directly where possible

Current migration bridges:

- frontend elaboration observations can surface as
  `frontend_elaboration_observation` compile adjudications
- frontend sparse slot bindings can surface as
  `frontend_sparse_slot_binding` compile adjudications
- frontend sparse payload leftovers can surface as
  `frontend_sparse_payload_leftovers` compile adjudications
- apply mutation events are preserved in replay metadata and capture artifacts
  but are not yet promoted into adjudications

## 6. Relationship To Evidence Claims

The intended flow is:

1. compiler emits observations
2. evidence layer groups and scores candidate claims from those observations
3. claims are promoted, blocked, or defeated
4. outward proof tiers are emitted

So:

- observations are inputs
- claims are intermediate explanations
- proof tiers are outputs

## 7. Current Migration State

Implemented:

- Finland `elaboration_observations` from payload normalization
- Finland `sparse_slot_bindings` from subgroup elaboration
- Finland `sparse_payload_leftovers` from subgroup elaboration
- replay-meta preservation of those observations
- replay-meta preservation of sparse slot bindings
- replay-meta preservation of sparse payload leftovers
- compile-result/adjudication bridge for frontend elaboration observations
- compile-result/adjudication bridge for frontend sparse slot bindings
- compile-result/adjudication bridge for frontend sparse payload leftovers
- `ApplyMutationEvent` at `apply_op(...)`
- replay-meta preservation of `apply_mutation_events`
- capture-artifact preservation of all three streams
- evidence bundles now expose a compact `compiler_observations` summary with:
  - frontend observation counts/kinds/stages
  - frontend sparse-slot-binding counts
  - frontend sparse-leftover counts
  - apply helper counts
  - section-bisect rows where frontend/apply observations support or block
    blame-step reasoning
- the evidence layer can now also use same-section sparse payload leftovers as
  an additional frontend-ambiguity corroborator when no stronger same-section
  frontend observation kind already explains the blamed step
- evidence now has a small in-code adapter that normalizes raw frontend/apply
  streams into section-friendly observation records before summarizing them or
  matching them back onto blamed sections
- that adapter now includes `frontend_sparse_slot_binding` as a first-class
  normalized frontend observation family for section-first reporting
- same-section sparse slot bindings now also survive into section-bisect
  support rows and section-claim support payloads as corroborating evidence,
  still without becoming a separate outward claim kind
- evidence bundles now also expose `section_claims` built on top of those
  section-friendly records and section-bisect support, while still keeping
  outward proof tiers conservative

Not yet implemented:

- full section-scoped evidence claim construction directly from normalized
  observation records
- conformance fixtures that assert observation streams explicitly

## 8. Next Good Steps

- let evidence consume frontend elaboration observations first, but only as
  corroborators / replay-demotion inputs until the vocabulary stabilizes
- later, let apply mutation events support replay-invariant and blame-step
  reasoning without forcing whole-tree diff reconstruction

# Manual Compilation Claims

Status: living spec, intentionally partial.
Kind: normative.

Purpose:

- define how LawVM may accept human- or LLM-assisted compilation without
  turning replay into hidden editorial guessing
- separate source reconstruction from legal semantic compilation
- preserve deterministic replay as the only executor of accepted legal state

Related:

- [CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [COMPILER_OBSERVATION_STREAM.md](COMPILER_OBSERVATION_STREAM.md)
- [SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md](SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md)

## 1. Core Rule

A manual or LLM-assisted step may propose legal meaning.
It may not directly mutate legal state.

The only executable artifact is still a validated canonical LawVM program:

```text
source witnesses
  -> deterministic extraction
  -> unresolved work item
  -> manual / LLM compilation claim
  -> deterministic validator
  -> canonical operations or typed non-replayable finding
  -> deterministic replay
```

If the validator cannot prove that a claim is supported by the source witnesses
and target state, the claim remains rejected or unresolved. Replay must not
recover the intended meaning from prose after validation fails.

## 2. Two Different Claim Layers

### 2.1 Source reconstruction claim

Used when the source witness itself is not already reliable machine-readable
law, for example scanned paper, OCR output, or LLM-converted PDF/XML.

A source reconstruction claim says:

- this scan/page/region contains this text
- this text has this legal-unit structure
- these coordinates, image hashes, and source artifacts support the claim

It does not say what amendment operations the text performs.

Required evidence:

- source artifact identifier and stable content hash
- page or region locator where available
- reconstructed text and structure
- production method, for example OCR engine, LLM model, human transcription, or
  double-keyed review
- confidence or review state
- reviewer/signer identity when human-reviewed

### 2.2 Semantic compilation claim

Used when the text/source is available but LawVM cannot deterministically lower
it to unambiguous operations.

A semantic compilation claim says:

- this source phrase has this action family
- these exact target addresses or facets are affected
- this exact old text, new text, structural payload, extent, and temporal scope
  are claimed
- this uncertainty is unresolved or non-replayable if no unique operation is
  justified

It must lower to canonical operations, typed source-pathology records, or typed
non-replayable findings. It must not remain a free-form instruction consumed by
replay.

## 3. Claim Shape

A manual compilation claim should be reviewable as data.

Minimum fields:

- stable claim id
- claim kind: `source_reconstruction`, `semantic_compile`,
  `non_replayable_finding`, or `claim_rejection`
- jurisdiction
- affected statute and affected target surface
- affecting source artifact and provision
- source witness locators and hashes
- quoted source witness snippets, bounded and sufficient for review
- proposed canonical operations or proposed finding
- action family and target facet
- temporal and applicability scope
- claimant: human, LLM, tool, or combined review lane
- validator version and validation result
- status: `proposed`, `validated`, `rejected`, `superseded`, or `withdrawn`

The claim may contain prose explanation, but the executable part must be typed.

## 4. Validator Contract

The validator is deterministic.

It should check:

- source witness exists in the archive or reconstructed-source ledger
- source quote is traceable to the claimed artifact
- action family is compatible with the source verb/effect family
- target address or facet exists, or the claim explicitly records why it does
  not
- old text exists where a text replacement or deletion claims it
- structural payload belongs to the claimed target and does not smuggle
  unrelated siblings
- extent, commencement, expiry, and applicability dimensions are represented or
  explicitly unresolved
- changed paths are inside the target region, declared migration paths,
  declared recovery paths, or declared editorial projection paths
- no claim converts one action family to another without an explicit finding

Validation may be incomplete in early implementations, but incompleteness must
be explicit. A claim accepted under weak validation is not equivalent to a
fully source-proved deterministic compile.

### 4.1 UK Provenance-Only Claim Validation

The UK tool `lawvm uk-semantic-claims-validate INPUT.jsonl` validates proposed
`lawvm.uk_semantic_compile_claim.v1` rows without making them executable.

The current validator checks only:

- required semantic-claim schema fields
- required source-witness hash presence
- source-preview hash self-consistency when the claim or matched workqueue row
  carries both bounded source preview text and its declared SHA-256 hash
- proposed outcome kind shape
- minimal canonical-operation shape for `canonical_operations` outcomes:
  operation id, canonical `StructuralAction`, target, and explicit
  `mutation_boundary.changed_paths` / `target_region` declarations
- duplicate canonical-operation ids are rejected within a claim, so a weak
  claim cannot collapse multiple proposed operation instances by accident
- optional canonical-operation reference declarations are duplicate-sensitive:
  repeated `destination`, `occurrence_ids`, or `removed_child_ids` entries reject
  before any family-specific proof semantics interpret them
- mutation-boundary path declarations are duplicate-sensitive:
  `changed_paths`, `target_region`, and declared exception paths reject repeated
  path entries before containment checks are interpreted
- static mutation-boundary containment for canonical-operation claims:
  `changed_paths` must sit under `target_region` unless the claim explicitly
  declares migration, recovery, or editorial-projection exception paths
- declared migration, recovery, or editorial-projection exception paths must
  carry a matching rule, reason, or event/observation id; exception paths are
  not self-justifying ownership
- minimal non-operation outcome shape: non-replayable findings, source
  pathologies, oracle adjudications, and requests for more source evidence must
  carry typed payloads rather than empty `outcome_kind` placeholders
- optional match against exported `lawvm.uk_manual_compile_frontier.v1`
  workqueue provenance via `--workqueue-jsonl`
- duplicate identical workqueue rows are tolerated, but conflicting rows with
  the same `work_item_id` reject validation; claim/workqueue matching must not
  depend on first-row or last-row input order
- identity-only workqueue matching also tolerates exact repeated rows but keeps
  conflicting rows for the same `(statute_id, effect_id,
  manual_compile_rule_id)` ambiguous until the claim supplies `work_item_id`
- consistency of work-item identity, manual-frontier rule, action family,
  source-preview hash, and affecting/affected provision fields when those fields
  are present in the matched workqueue row
- optional consistency of declared `source_text_preconditions` against supplied
  claim/workqueue source previews: a claim may require exact source snippets,
  optionally with snippet SHA-256, source-preview occurrence counts, and
  `after_precondition_ids`/`before_precondition_ids` relationships between
  source precondition ids; the validator rejects the claim if the supplied
  source witness does not carry them, if its non-overlapping snippet count
  violates a declared exact/min/max occurrence bound, if referenced source
  precondition ids are missing or duplicated, if ordering reference lists repeat
  the same precondition id, or if the ordered snippets do not appear uniquely in
  the same supplied source preview in the declared order; multi-occurrence
  ordering requires a future explicit ordinal/span claim rather than
  first-occurrence matching
- consistency of declared claim target context with matched non-executable
  template carriers: when a template publishes `source_target_address` or
  `destination_address`, the claim must echo the same address in top-level
  claim context or proposed-outcome target context
- consistency of canonical-operation target paths with matched non-executable
  template carriers: when a template publishes `source_target_address` or
  `destination_address`, each operation target must sit under one of those
  declared carriers
- optional consistency with a supplied `lawvm.uk_live_target_index.v1` live-state
  target index via `--live-targets-jsonl`: replace/repeal/text/heading/renumber
  claims must target an existing path, while insert claims must have an existing
  parent carrier
- optional consistency of declared `live_target_preconditions` against supplied
  target fingerprints: a claim may require a `subtree_sha256` or `text_sha256`
  for any live target path, and the validator rejects the claim if live
  precondition ids are duplicated or the supplied live index does not match
- supplied live-target indexes must not contain conflicting fingerprints for the
  same statute/path; identical repeated fingerprints are tolerated, but
  inconsistent duplicates reject validation before any claim precondition is
  checked
- optional consistency of declared `operation_family_proofs`: each proof row
  must name a proof id, match the claim action family, reference existing
  operation ids, reference declared validator-check ids, reference at least one
  declared source/live precondition, and carry a non-proving status
- matched claim-template obligation lists are declaration surfaces too:
  duplicate `required_validator_checks`, `required_ownership`, or
  `required_operation_family_proof_semantics` entries reject the claim/workqueue
  match before required obligations are interpreted as sets
- proof reference lists are declaration surfaces, not mathematical sets:
  duplicate `operation_ids`, `validator_check_ids`,
  `source_text_precondition_ids`, `live_target_precondition_ids`, or
  `live_target_precondition_paths` reject the claim before proof semantics are
  considered. Recognized family-specific proof reference, ownership, source,
  and live-path fields are duplicate-sensitive for the same reason
- operation-family proof semantics resolve `live_target_precondition_ids` only
  to the paths on those referenced live precondition rows; unrelated live
  preconditions declared elsewhere in the claim do not widen the proof's target
  carrier set
- family-specific live path fields, such as appropriate-place anchor paths and
  cross-container source/destination carriers, must also sit inside the proof's
  referenced live carrier set rather than merely existing elsewhere in the claim
- validator-check ids and ownership ids are unique declaration ids within a
  claim; duplicate ids are rejected before proof or template matching, so a
  proof obligation cannot be satisfied by a collapsed set of inconsistent rows
- live-target precondition ids are also rejected as duplicate schema
  declarations even when no live-target index is supplied yet
- presence of every `required_ownership` id listed by a matched non-executable
  claim template, so a claim must declare the source/target/mutation-boundary
  surfaces it claims to own
- presence of every `required_validator_checks` id listed by a matched
  non-executable claim template, so a claim cannot pass while omitting a known
  family-specific proof obligation
- explicit non-proving status for each declared template ownership claim; this
  weak validator rejects claims that label ownership as `passed`, `proved`,
  `validated`, or `verified`, treating case and surrounding whitespace as
  non-semantic
- explicit non-proving status for each declared template proof obligation; this
  weak validator rejects claims that label an obligation as `passed`, `proved`,
  `validated`, or `verified`, treating case and surrounding whitespace as
  non-semantic

It emits `lawvm.uk_semantic_compile_claim_validation.v1` rows. An accepted row
uses `validator_status=validated_provenance_only`,
`replay_authorized=false`, and `executable=false`. This status means the claim
is well-formed, carries operation-boundary declarations where applicable, keeps
claimed changed paths within declared target or exception regions, declares
boundary-exception paths with their own witness rule/reason/id, declares
template carrier context and target containment, declares required ownership
surfaces and validation obligations without pretending this validator proved
them, and matches the supplied workqueue provenance; it does not mean the
proposed canonical operations are source-proved or replayable.

If `--live-targets-jsonl` is supplied and the live-state check passes, accepted
rows use `validator_status=validated_provenance_and_live_targets_only`. This is
still non-executable and keeps `replay_authorized=false`; it proves only that the
claim is not disconnected from the supplied target index.

If the claim declares matching `source_text_preconditions`, accepted rows use
`validator_status=validated_provenance_and_source_text_only` when no live-target
index is supplied. If both source-text preconditions and live-target gates pass,
the accepted status combines those surfaces. These rows remain non-executable
and keep `replay_authorized=false`; exact source snippets in a bounded preview
optional source-preview occurrence counts, and optional source-snippet order
relationships are evidence, not proof that the whole operation family is
replay-safe. Ordered snippets must be unique in the checked source preview; if
they are repeated, the validator rejects the order claim until an explicit
ordinal/span claim form exists. Ordered-snippet reference lists are also
duplicate-sensitive, so repeating the same `after_precondition_ids` or
`before_precondition_ids` reference is rejected before interpreting the
relationship.

If the claim also declares matching `live_target_preconditions`, accepted rows
use `validator_status=validated_provenance_live_targets_and_preconditions_only`.
This is still non-executable and keeps `replay_authorized=false`; it proves only
that the claim's declared live-state hashes match the supplied target index.

If the claim declares `operation_family_proofs`, the validator checks only that
the proof rows are internally wired to the claimed operation family, operations,
validator checks, and source/live preconditions. This is a proof-plan integrity
check, not proof of legal sufficiency. Rows whose proof status says `passed`,
`proved`, `validated`, or `verified` are rejected by this weak validator; the
comparison is case-insensitive after trimming surrounding whitespace.
Validation rows also carry `operation_family_proof_count`,
`operation_family_proof_semantics`, and `operation_family_proof_families`, and
the validation report summarizes semantic/family counts, so batch review can see
which proof plans were checked without reparsing the input claim ledger.
Within family-specific proof semantics, a referenced live precondition id
authorizes only that precondition row's `path`; adding another live precondition
to the claim does not expand a proof unless the proof explicitly references it
by id or path. Live precondition ids must be unique so an id cannot merge
multiple carrier paths. Family-specific live path fields are also scoped to the
same referenced live carrier set.
The first opt-in family semantic,
`table_surface_insert_anchor_and_live_carrier`, additionally checks that a
`table_surface_mutation` proof references source text evidence, a live carrier
precondition, and only insert operations whose target parent sits under that
declared live carrier. It still does not authorize replay.
`text_rewrite_source_preimage_and_live_target` similarly checks text-rewrite
families that reference source preimage evidence and a live target precondition;
referenced operations must be text or heading rewrite actions and must target
the declared live target itself.
`structural_insert_source_payload_and_live_parent` checks bounded non-table
structural insertion families (`structural_sibling_insert`,
`definition_entry_insert`, `index_entry_insert`, and
`schedule_part_wrapper_insertion`) that reference source payload evidence and a
live parent precondition; referenced operations must be inserts whose target
parent sits under the declared live parent. It still does not authorize replay.
`schedule_list_entry_anchor_boundary_claim` checks
`schedule_list_entry_mutation` proofs that reference source evidence for the
entry anchor and inserted/replacement entry payload, declare the source-named
entry anchor, list carrier, and sibling insertion/replacement boundary, require
operation-level entry anchor/position plus entry label/text identity, and keep
entry insert/replacement operations within declared live schedule-entry
carriers. It still does not authorize replay.
`definition_entry_insert_term_boundary_claim` checks `definition_entry_insert`
proofs that reference source evidence for the inserted definition term and
complete definition-entry payload, declare inserted-term, payload, definition
list, and insertion-position/list-end ownership, require operation-level
definition term, payload, and insertion position, and keep definition-entry
inserts under declared live definition-list carriers. It still does not
authorize replay.
`savings_qualified_omission_applicability_scope` checks
`savings_qualified_text_omission` proofs that reference separate source
preconditions for the omitted reference and the savings/applicability condition,
plus a live text-carrier precondition; referenced operations must be text
omission actions and must declare an applicability or savings scope. It still
does not authorize replay.
`whole_act_listed_enactments_scope_and_exclusions` checks
`whole_act_listed_enactments_text_patch` proofs that reference source evidence
for listed-enactment membership and quoted preimages, declare same-schedule/
same-act exclusion ownership, exclude title/short-title surfaces, and target
only declared live text carriers with whole-Act text patch actions. It still
does not authorize replay.
`appropriate_place_anchor_or_ordering_claim` checks `appropriate_place_mutation`,
`definition_entry_insert`, and `index_entry_insert` proofs that reference source
payload evidence, declare a validated predecessor/successor anchor or ordering
claim, reference a live anchor or an ordering rule listed in the proof's
declared validator checks, keep explicit anchor live paths inside the proof's
referenced live carriers, and emit only insert operations under declared live
parent carriers. It still does not authorize replay.
`range_to_container_source_range_payload_and_lineage` checks
`range_to_container_substitution` proofs that reference source-range evidence
and container-payload evidence, declare lineage/migration ownership, require
replacement operations to declare migration paths and a lineage/migration event
id, and target only declared live container carriers. It still does not
authorize replay.
`table_repeal_or_omission_boundary_preservation` checks
`table_repeal_or_omission` proofs that reference table-surface source evidence,
declare the repealed row/column/cell boundary and unclaimed-table preservation,
and target only declared live table carriers with table repeal or text-omission
actions. It still does not authorize replay.
`cross_container_renumber_source_destination_and_lineage` checks
`cross_container_renumber_migration` proofs that reference source evidence for
both the source target and destination target, declare lineage/migration
ownership plus destination-boundary ownership, require renumber operations to
declare a destination, migration paths, and a lineage/migration event id, and
keep source and destination paths inside the proof's referenced live carriers.
It still does not authorize replay.
`amendment_program_target_source_payload_and_boundary` checks
`amendment_program_target_mutation` proofs that reference source target evidence
and inserted-payload evidence, declare amendment-program target-boundary and
payload ownership, require a declared amendment-program target id or source
target on the operation, and keep insert/replacement operations under declared
live amendment-program carriers. It still does not authorize replay.
`definition_child_text_tail_boundary_claim` checks
`definition_child_and_tail_substitution` proofs that reference source evidence
for the definition term, child label, tail connector, and replacement payload,
declare the child-text, post-child-tail, and replacement-payload boundaries, and
keep bounded definition-child text replacement operations under declared live
definition carriers. It still does not authorize replay.
`definition_child_structural_payload_boundary_claim` checks
`definition_child_structural_substitution` proofs that reference source evidence
for the definition term, child label, and replacement child payload, declare the
definition-term scope, child identity, replacement-child payload shape, and
tail-connector boundary when claimed, and keep bounded structural replacement
operations under declared live definition carriers. It still does not authorize
replay.
`definition_child_structural_insert_boundary_claim` checks
`definition_child_structural_insert` proofs that reference source evidence for
the definition term, anchor child, inserted payload, and existing tail connector,
declare definition scope, anchor identity, inserted-payload shape, tail-connector
boundary, and connector migration/preservation ownership, require operation-level
definition term, anchor child, inserted child, and connector handling, and keep
structural inserts under declared live definition carriers. It still does not
authorize replay.
`referent_qualified_occurrence_scope_claim` checks
`referent_qualified_text_substitution` proofs that reference source evidence for
the referent entity, quoted preimage terms, and replacement text, declare
referent/coreference ownership, require operation-level referent scope and
occurrence ids, and keep text replacement operations on declared live text
carriers. It still does not authorize replay.
`mixed_body_heading_split_boundary_claim` checks
`mixed_body_heading_text_substitution_split` proofs that reference source
evidence for the body target, heading facet, per-surface preimage, and
replacement, declare body/facet split ownership plus unclaimed-surface
preservation, require separate body-text and heading-facet operations, and keep
both operations on declared live split-surface carriers. It still does not
authorize replay.
`structural_child_range_source_payload_boundary_claim` checks
`structural_child_range_substitution` proofs that reference source evidence for
the child range, removed children, and replacement payload, declare range,
removed-child, payload-shape, and parent tail/text boundary ownership, require
operation-level child-range and removed-child identity, and keep range
substitution operations within declared live child-range carriers. It still does
not authorize replay.
`source_carried_multi_subunit_boundary_claim` checks
`source_carried_multi_subunit_text_rewrite` proofs that reference source
evidence for the child-unit set, per-child preimage, and replacement/repeal
payload, declare child-unit boundary ownership, require operation-level child
unit identity, and keep text rewrite operations on declared live child-unit
carriers. It still does not authorize replay.
`source_carried_child_tail_boundary_claim` checks
`source_carried_child_tail_text_rewrite` proofs that reference source evidence
for the child anchor, tail scope, and replacement/repeal payload, declare the
child anchor, tail preimage/repeal scope, and payload boundaries, require
operation-level child-anchor and tail-boundary identity, and keep text rewrite
operations on declared live child-tail carriers. It still does not authorize
replay.
`source_carried_structured_payload_boundary_claim` checks
`source_carried_structured_text_patch` proofs that reference source evidence for
the parent formula anchor and structured payload units, declare parent-formula,
payload-unit, and child-target boundary ownership, require operation-level
payload-unit or child-target identity, and keep structured insert/replacement
operations within declared live child-target carriers. It still does not
authorize replay.
`source_carried_structured_tail_boundary_claim` checks
`source_carried_structured_tail_substitution` proofs that reference source
evidence for the tail range and structured replacement payload units, declare
tail-range, structured-payload, child-target, and flattened-patch replacement
boundaries, require operation-level tail-range plus payload-unit identity, and
keep structured tail substitution operations within declared live tail/child
carriers. It still does not authorize replay.

`lawvm uk-live-target-index STATUTE_ID... --source current|enacted --out PATH`
exports archive-backed `lawvm.uk_live_target_index.v1` rows for this gate. The
exporter uses canonical `kind:label/...` legal paths and collapses unlabeled
presentation wrappers such as body/crossheading/p1group carriers, so a section
inside those wrappers is indexed as `section:1`, not as a wrapper-dependent
transport path. Rows also carry `target_fingerprints` keyed by path, including
direct-text and subtree SHA-256 hashes. These hashes are live-state preconditions
for claim validation, not replay authority.

Rejected rows are blocking claim-validation findings. They do not change the
compiler result. Replay may consume semantic claims only after a later
deterministic validator proves the claimed operations or non-replayable finding
against source witnesses and live target state.

## 5. Strictness And Trust

Manual and LLM claims are an authority layer, not a replacement for source
authority.

Strict mode may reject all manual claims unless the caller opts into a specific
trusted claim ledger. Quirks/manual mode may replay validated claims, but must
preserve the claim id and validation status in operation provenance.

Benchmark reports must distinguish:

- deterministic source-only replay
- replay with validated manual/human claims
- replay with LLM-proposed but unreviewed claims
- replay with reconstructed source

These modes should not be collapsed into one score.

## 6. Non-Replayable Outcomes Are First-Class

The correct output of a manual work item may be:

- canonical operations
- a source-pathology finding
- an oracle/editorial adjudication
- a non-replayable legal-state finding
- a request for more source evidence

For example, if a table repeal row names a target but the public source lacks
the old text needed to identify the deletion safely, the claim should say
`non_replayable_from_available_public_sources` rather than inventing a text
patch.

## 7. Scanned-Paper Frontends

For scanned-paper jurisdictions, such as a future Aruba frontend, LawVM should
not treat OCR/LLM XML as source truth.

The source pipeline should be:

```text
official scan / paper PDF
  -> source reconstruction claim
  -> reviewed machine-readable source witness
  -> deterministic frontend parse
  -> semantic compilation claim only for remaining ambiguity
  -> validator
  -> canonical operations
```

The reconstructed source witness must keep provenance back to the scan.
Page coordinates and image hashes are part of the legal evidence trail, not
debug decoration.

# High Order Invariant Reuse Audit

Status: living audit.
Kind: descriptive planning.
Date: 2026-05-28.

Purpose:

- identify invariant, diagnostic, normalization, and evidence logic that is
  duplicated across frontends or tools;
- rank candidates for promotion into shared core;
- avoid promoting jurisdiction grammar or source-specific heuristics too early.

This audit intentionally ranks reuse by semantic leverage, not by line-count
reduction.

## Current Shared Surfaces

Already useful:

- `src/lawvm/core/tree_ops.py::check_invariants`
- `src/lawvm/core/replay_lints.py`
- `src/lawvm/core/mutation_boundary.py`
- `src/lawvm/core/timeline_invariants.py`
- `src/lawvm/core/phase_result.py`
- `src/lawvm/core/observation_registry.py`
- `src/lawvm/core/adjudication_evidence.py`

Recent improvement:

- `src/lawvm/core/diagnostic_records.py::diagnostic_detail` now owns the
  shared detail-envelope defaults for `rule_id`, `phase`, `blocking`,
  `strict_disposition`, and `quirks_disposition`; UK lowering and the central
  UK replay action/target detail builder use it while keeping their local
  adjudication carriers.
- UK source acquisition/extraction diagnostics now also delegate their outward
  envelope defaults to `diagnostic_detail` while preserving local source-lane
  rule IDs and payload fields.
- UK effect-feed and metadata acquisition/parse diagnostics now delegate their
  outward envelope defaults to `diagnostic_detail` while preserving local feed
  locator and parser-error payload fields.
- UK lowering-order diagnostics for source-citation ordering and same-target
  text-patch preimage chains now delegate their outward envelope defaults to
  `diagnostic_detail`.
- UK affecting-act prefetch source-pathology and source-witness event builders
  now delegate their outward envelope defaults to `diagnostic_detail`.
- UK authority-filter lowering diagnostics now delegate their outward envelope
  defaults to `diagnostic_detail` while preserving authority-mode payloads.
- UK commencement-filter observations now delegate their outward envelope
  defaults to `diagnostic_detail` while preserving commencement target/date
  payloads.
- UK payload-identity normalization records now delegate their outward envelope
  defaults to `diagnostic_detail` while preserving strict blocking and
  synthesized-EID samples.
- UK lowering filter, source-pathology filter, manual-frontier, PIT-date,
  metadata-only, source-pathology classification, and applicability-filter
  records now delegate their outward envelope defaults to `diagnostic_detail`.
- UK source-backed temporal recovery records now delegate their outward
  envelope defaults to `diagnostic_detail`.
- UK source-parse shape-preservation observations now delegate their outward
  envelope defaults to `diagnostic_detail`.
- UK bootstrap effect-feed acquisition diagnostics now delegate their outward
  envelope defaults to `diagnostic_detail` while preserving nested source
  details.
- UK benchmark exception and effect-feed-count diagnostics now delegate their
  outward envelope defaults to `diagnostic_detail`.
- UK manual-frontier validation JSONL rows now delegate their outward envelope
  defaults to `diagnostic_detail`, including input-error and stale-effect
  rows, while preserving the workqueue/reporting schema fields.
- `validate_diagnostic_detail(...)` now validates the shared diagnostic
  envelope independently of frontend-local payload fields.
- blocking strict-disposition vocabulary is now shared between diagnostic
  envelope validation and corpus evidence-row validation.
- blocking/strict-disposition validation is now one shared helper reused by
  diagnostic detail validation and corpus evidence-row validation while
  preserving row-specific issue text.
- mutation-boundary partitioning is now shared and reused by Finland apply
  events;
- operation-aware storage boundaries now encode that insert/repeal/renumber
  child-list mutations show up at parent paths.
- core mutation events now expose a shared path-set report that partitions
  touched paths through target, recovery, and migration regions; Finland apply
  accounting projects this shared report into its existing report shape.
- `TreeInvariantViolation` now exposes a stable dict projection; Finland and
  Sweden replay metadata emit typed tree-invariant records beside legacy
  strings, and `scripts/audit_invariants.py` prefers typed metadata when
  available.
- materialization duplicate regression helpers now consume typed tree invariant
  records instead of maintaining a parallel duplicate-child scanner.
- core comparison normalization supports placeholder-equivalence rules, and
  Estonia comparison text normalization delegates execution to the core
  pipeline while keeping the EE rule taxonomy local.
- Sweden comparison-only dash/editorial/inline-numbering projection now
  delegates its named presentation rules to the core comparison-normalization
  pipeline while keeping Sweden's rule taxonomy local.
- Norway comparison-only spacing, footnote-marker, hyphen-gap, and placeholder
  dash-tail projection now delegates its named presentation rules to the core
  comparison-normalization pipeline while keeping Norway's suppression logic
  local.
- Finland Finlex oracle comparison-only kumottu-stub, amendment-date
  parenthetical, and previous-wording marker cleanup now delegates its named
  presentation rules to the core comparison-normalization pipeline while
  keeping broader editorial cleanup opt-in.
- core comparison normalization now validates rule shapes before applying
  them, so missing regex patterns, translation tables, or literal preimages
  fail loudly instead of becoming silent no-op projection rules.
- core comparison normalization now exposes an ordered rule-set validator with
  duplicate-name detection, and current Estonia, Norway, Sweden, Finland, and
  Open Law comparison rule sets are pinned by that shared validator.
- comparison normalization execution now runs the ordered rule-set validator,
  so duplicate rule names cannot produce ambiguous `fired_rules` output.
- UK replay now has an opt-in core `MutationEvent` sink for central node
  replacements, removals, and ordinary insertions via `_replace_node_in_statute`,
  `_remove_node`, `_record_child_inserted`, and `_record_supplement_inserted`;
  whole-act repeal is also recorded as a root-path removal.
- UK direct table row, table column, schedule-table row, and schedule-list
  entry splices now emit conservative container-scoped mutation events. Table
  row events record the table path instead of inventing row-level identity for
  unlabeled duplicate table rows.
- UK table-cell ordered-list child insertion now routes the cell rewrite
  through the central node-replacement helper, so the replay mutation event
  records the exact cell path instead of mutating text and source attrs in
  place.
- UK schedule-list entry insert/repeal/replace helpers now route child-list
  replacement plus mutation-event recording through one local wrapper; grouped
  schedule-entry insertion is covered by a mutation-event regression.
- UK same-parent sibling renumber replay now emits a renumber-specific mutation
  event with old and new paths instead of reporting the destination as an
  ordinary insertion.
- UK same-parent sibling renumber replay now also reindexes the moved subtree
  and bumps the structure-mutation serial, so warm eId lookups and
  post-renumber invariant diagnostics observe the changed tree.
- UK same-provision descendant renumber replay now emits a renumber-specific
  MutationEvent (outcome=renumbered_node, helper=_apply_same_provision_descendant_renumber)
  carrying the lineage pair: old_path (the source provision, e.g. section:12)
  → new_child_path (the relocated content, e.g. section:12/subsection:1).
  Previously _replace_node_in_statute emitted only a generic replaced_node event
  with no lineage semantics.  The new event is emitted immediately after the
  replace succeeds, using the old_path captured before the replace clears the
  eId lookup index.  Rule ID `uk_replay_descendant_renumber_provision` is
  registered in UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS in source_adjudication.py
  (blocking=False, proceed in strict mode — this is a legitimate lineage operation,
  not a heuristic recovery).  The generic replace event is preserved; the renumber
  event supplements it with §1.6 migration provenance.  PIT materialization can
  consume the renumbered_paths pair to reconstruct that the source provision's
  content was relocated to the child path.  Covered by
  tests/test_uk_replay_descendant_renumber_mutation_event.py (10 tests: positive,
  witness fields, PIT-shape, rule-ID, negative-sibling, negative-no-dest,
  negative-existing-child, no-collection-guard, executor-direct).
- UK table-column insertion now stages spanning-cell and row-child edits until
  the whole column boundary is proven, so an unresolved short payload cannot
  leave partial table mutations outside an applied mutation event.
- UK table-column insertion now also clears replay lookup state and bumps the
  structure-mutation serial after applying the proven splice, so post-op
  invariant diagnostics do not treat the table-column mutation as invisible.
- UK content-payload structural replacements now route through the central
  node-replacement helper, so lead-text replacement events are visible on the
  shared mutation-event stream.
- UK source-carried labelled-child text-substitution recovery now rebuilds the
  recovered parent through the same central node-replacement helper instead of
  mutating text and children in place.
- UK node replacement now treats recursive kind/label shape changes as
  structural mutations, so label-changing replacements and descendant-shape
  replacements rescan post-op invariants instead of relying only on immediate
  child-shape changes.
- UK post-op invariant adjudications now include the latest same-op mutation
  event accounting summary when mutation events are being collected, making
  the causal helper, touched paths, and allowed/unexplained path partition
  visible without changing replay control flow.
- UK mutable sorted child insertion now refuses same-kind/same-normalized-label
  replacement instead of inheriting the lower-level canonical insertion
  helper's destructive replacement semantics.
- UK body-root insertion fallbacks now route through the guarded mutable insert
  helper, and same-parent sibling renumber now checks for a destination sibling
  collision before removing the source node.
- UK top-level supplement insertion now uses the same guarded sorted-insert
  surface as child insertion, so duplicate schedule fallback insertion cannot
  replace or append over an existing same-label supplement while emitting a
  successful recovery adjudication.
- UK explicit-index insertion paths now share the same same-kind/same-label
  collision guard as sorted insertion, so routed, predecessor-based, and
  definition-child sibling inserts cannot introduce duplicate labelled
  structural siblings by bypassing the sorted helper.
- UK source-carried structured-tail recovery now respects the guarded insert
  helper's refusal result before recording a child insertion event or
  successful recovery adjudication.
- UK nested descendant replacement rebuilding is now copy-on-write, so fallback
  replacement paths no longer mutate a live body/supplement subtree before
  `_replace_node_in_statute` records the replacement event.
- unused UK mutable replay helpers for direct text and text+children mutation
  were removed after their callers moved behind central mutation-event paths.
- mutation path type aliases (`TreePaths`, `RenumberedTreePaths`) now live in
  the core mutation-boundary surface instead of Finland-local apply helpers.
- selected UK production path annotations now use the shared `TreePath` alias
  instead of spelling legal-address paths as nested tuple types.
- `TreePathStep` now lives beside `TreePath`, letting frontends type mutable
  path builders without repeating `tuple[str, str]` for legal tree steps.
- Finland editorial/corrigendum address builders and UK replay/addressing
  root-path helpers now use shared tree-path aliases where the values are
  actual legal tree path steps, leaving parser pair/range tuples local.
- tree/property tests now use the public `default_label_sort_key(...)` wrapper
  instead of importing the private core `_default_sort_key` helper, keeping
  public label utility behavior covered without private API coupling.
- `LegalAddress.has_prefix(...)` now owns the core path-prefix plus facet
  matching invariant used by timeline address and temporal scope helpers.
- `LegalAddress.has_path_prefix(...)` now owns path-only prefix matching for
  timeline lineage/materialization and Finland timeline-target checks that
  intentionally ignore facets.
- UK replay preparation now preserves rejected operation objects beside their
  blocking adjudications, making the prepare filter lossless for audit tooling.
- mutation-event path-set reporting now validates declared allowances before
  classification, so non-target allowances with paths must carry named rule IDs.
- mutation-event path-set reporting now also validates tree-path shape for
  touched paths and allowed effect-region paths before evidence classification.
- shared changed-path partitioning now validates changed-path and allowed-prefix
  shape before classifying boundary coverage.
- diagnostic detail payloads now reject frontend-local `detail` keys that would
  override shared envelope fields such as `rule_id`, `phase`, or dispositions.
- mutation-event accounting result/report logic now has a core home; Finland
  apply events re-export the shared accounting surface for compatibility.
- mutation-event accounting now classifies applied/failed/skipped outcome
  families centrally, so frontend-local outcome strings can feed shared
  accounting without being rewritten at the source event.
- lossless filter results now have a small core carrier; UK authority filtering
  uses it while preserving existing diagnostics and returned kept-op lists.
- UK replay preparation now exposes its existing accepted/rejected operation
  lanes as a shared `FilterResult` projection while preserving UK-specific
  blocking adjudications as the authoritative replay evidence.
- bidirectional path-relation helpers now live in core; Norway verification
  delegates its touched-divergence relation predicate while keeping local
  ignored-container and symbolic-label policy.
- strict tree-path prefix classification now lives in core; Norway primary
  divergence partitioning delegates prefix/descendant suppression to the
  shared path predicate.
- filtered divergence partition records now live in core verification contracts;
  Norway verification aliases them while keeping local suppression rules.
- duplicate-preserving source-path indexing now has a core helper; New Zealand
  agreement and version-diff surfaces reuse it to avoid source-path overwrites.
- source-lane selection evidence now has a neutral core carrier; Sweden
  official-PDF fallback acquisition emits that shared projection while keeping
  Swedish lane policy local.
- Finland operative-text acquisition now exposes its preamble/body/fallback
  lane decision through the shared source-lane evidence carrier in phase
  witnesses while leaving lane policy local.
- UK affecting-act current-vs-enacted XML source selection now uses the shared
  source-lane evidence carrier for selected and rejected lane attempts while
  preserving UK-local locators, sizes, and previews.
- UK article-plus-attached-schedule affecting-source extraction now uses the
  shared source-lane carrier to show the article context lane and selected
  schedule-payload lane without changing extraction policy.
- UK single-child amendment payload, enacted schedule-table row payload, and
  compound BlockAmendment payload-only extraction diagnostics now use the same
  source-lane carrier while preserving their existing UK-local fields.
- source-lane selection evidence now validates that selected lanes are tied to
  an attempted lane, an explicitly selected attempt, or an explicit
  `no_source_lane_selected_*` failure lane.
- source-lane selection evidence now normalizes attempted lanes to immutable
  tuples and freezes nested detail payloads at construction, so later caller
  mutation cannot rewrite acquisition evidence.
- Estonia replay orchestration now nests shared source-lane selection evidence
  inside amendment fetch, amendment parse, temporal source scan, pending
  cancellation, and pending source-act commencement failures while preserving
  Estonia's blocking adjudication kinds and families.
- Estonia redactions-feed acquisition failures now also nest shared source-lane
  evidence for the failed RT feed lane, preserving the existing
  `RedactionsFeedDiagnostic` owner type.
- EU Cellar manifest request failures now nest shared source-lane selection
  evidence for the failed notice request lane while preserving the existing
  source-pathology row.
- New Zealand latest XML locator candidate rejections now nest shared
  source-lane selection evidence for rejected API version-detail lanes while
  preserving the existing NZ diagnostic rows.
- New Zealand API v0 HTTP acquisition failures now optionally carry the same
  source-lane sidecar in `NZAcquisitionDiagnostic` without changing existing
  source-pathology ownership.
- Estonia target-resolution parse rejection helpers now delegate shared
  diagnostic envelope fields to `diagnostic_detail` while preserving their
  local target-title and source-fragment payload.
- Finland Finlex latest-PIT sync diagnostics now delegate shared acquisition
  envelope fields to `diagnostic_detail` while preserving statute/version
  locator payloads.
- source-lane selection evidence now requires a selected lane either to match
  an attempted lane, to use an explicit `no_source_lane_selected_*` marker, or
  to record `selected_lane_route_from` plus `selected_lane_routing_rule` when a
  frontend projects a selected attempt into a routed lane.
- UK commencement-filter observations for unnumbered unique schedules and
  undated commencement-style rows now carry nested shared target-resolution
  and temporal-resolution projections while preserving the legacy flat fields.
- Norway amendment-index duplicate logical locator diagnostics now include a
  nested shared source-lane selection projection for both deterministic
  byte-identical duplicate selection and conflicting-duplicate blocking,
  while preserving Norway's source-pathology record as authoritative.
- Norway public-archive ingest duplicate locator diagnostics now include the
  same shared source-lane projection for the retained existing farchive witness
  and incoming duplicate archive member.
- `_witness_for_op` in `src/lawvm/uk_legislation/witness_sidecars.py` return
  type tightened from `object | None` to `Optional[UKLoweredOperationWitness]`
  (Sensor E Cluster D root-cause fix). The untyped-dict fallback branch was
  confirmed dead at 0 hits on the smoke corpus and removed. Downstream getattr
  chains collapsed to direct typed attribute access at ~14 sites across
  `authority_filter.py` (5 sites), `text_rewrite_fragments.py` (4 sites), and
  `replay_target_gaps.py` (5 sites). All try-except and defensive getattr on
  `LegalOperation.payload` / `provenance_tags` also removed from
  `_witness_for_op` itself. Adjudication counts identical (4532). ty green.
- source-version bracketing now has shared source-only window selectors; New
  Zealand archived XML date/change windows delegate the bracketing invariant
  while preserving NZ-specific row classes and truth-claim names.
- evidence row kind classification now lives with the shared evidence
  contracts; New Zealand evidence packs and generic report-query tooling reuse
  the same operation/finding row predicate.
- temporal-resolution evidence now has a neutral core carrier; UK
  source-backed SI commencement-date recovery and Norway temporal replay skips
  emit the shared status/date/source projection while keeping date extraction
  and commencement policy local.
- contingent temporal resolution can now distinguish unresolved trigger state
  from coverage-certified untriggered state in core activation/resolution
  status projection; certified-untriggered resolution facts must point to an
  authority source or trigger coverage certificate.
- the legal branch/authority axis now has an initial core contract:
  default enacted materialization remains branchless, while proposal/draft
  operations require a branch id and are excluded by the default enacted
  operation filter.
- branch impact projection rows now provide a small shared UI/API-facing
  payload for would-affect branch graph edges without claiming enacted legal
  effect or executing jurisdiction-specific proposal parsing in core.
- branch graph edges can now be projected from typed non-enacted operations,
  giving proposal/draft frontends a shared path from `LegalOperation` to
  would-affect graph export facts.
- branch impact projections can now be built directly from typed non-enacted
  operations, which is the minimal UI/API handoff for proposal-style demos.
- branch impact projections can now be text-enriched from frontend-supplied
  current/branch text maps without letting core infer source text or enacted
  authority.
- branch impact projections now reject manually supplied rows whose
  `branch_id` or `scenario_id` does not match the projection branch, and edge
  selection requires exact scenario agreement rather than branch-id-only
  matching.
- branch lifecycle events now record proposal/draft status history as graph
  facts, separate from enacted-state operation replay.
- branch lifecycle events now carry `scenario_id`; `CorpusGraph` rejects
  lifecycle scenario mismatches against the registered branch, and CSV/JSON-LD
  export preserves the scenario axis.
- `CorpusGraph` now rejects duplicate branch ids and dangling branch edge or
  lifecycle references, keeping proposal/draft graph claims internally
  referentially sound.
- `CorpusGraph` now freezes branch, branch-edge, and lifecycle collections at
  construction after type validation, preventing post-validation mutation from
  leaking dangling branch evidence into graph exports.
- branch graph edges and branch-impact projection rows now carry `scenario_id`,
  and `CorpusGraph` rejects branch-edge scenario mismatches against the
  registered branch so proposal variants cannot merge by branch id alone;
  branch-impact row ids include scenario ids when present.
- branch overlay operation selection now has a core helper for explicit
  current-law-plus-selected-branch materialization demos without promoting
  proposal claims into the default enacted lane.
- timeline compilation now enforces the selected branch/authority context at the
  core boundary for both operations and temporal events, recording
  `timeline.excluded_authority_context` instead of letting non-selected branch
  claims leak into default enacted PIT state.
- Neo4j graph export now includes branch nodes, branch would-affect edges, and
  branch lifecycle event tables so proposal/draft graph facts have a persistent
  export lane.
- JSON-LD graph export now includes branch resources, branch graph edges, and
  branch lifecycle events under the `lawvm:` namespace.
- `lawvm branch-demo` now emits a synthetic branch/authority payload for
  demonstrating default enacted vs selected proposal operation lanes.
- `OperationSource` now validates the same branch context invariant as branch
  selectors, so proposal/draft/consultation operations without explicit
  branch identity fail at the provenance boundary instead of later
  materialization or graph projection.
- branch contexts now reject scenario ids without branch ids; branch graph
  edges are projected only from proposal/draft/consultation operations;
  `CorpusGraph` requires branch-edge authority/status to match the registered
  branch; terminal lifecycle events require matching terminal status; JSON-LD
  branch-edge ids include scenario and target context to avoid graph-resource
  collisions.
- target-resolution evidence now has a neutral core carrier for source target,
  candidate count, selected target, status, confidence, and strict/quirks
  disposition; frontends still own candidate discovery and local fallback
  policy.
- target-resolution certificates now reject ordinary `resolved` selections
  whose selected target is not one of the listed candidates; fallback/recovery
  statuses remain the explicit lane for named target rebinding.
- UK source-text schedule-paragraph target overrides now include a nested core
  target-resolution certificate while preserving the existing UK lowering
  observation fields and authority policy.
- New Zealand instruction-workqueue evidence now projects its latest-oracle
  target lookup through the shared target-resolution carrier while preserving
  NZ-local status names and keeping replay/effect claims disabled.
- New Zealand operation-surface target candidate findings now carry nested
  shared target-resolution certificates for blocked address candidates,
  end-skeleton duplicate recovery, and attached-heading context recovery while
  preserving the existing NZ witness rows and disabled replay/effect claims.
- target-resolution certificates now validate the shared status and
  scope-confidence vocabulary at construction, and resolved/recovered statuses
  must carry a counted selected candidate.
- target-resolution certificates now normalize candidate lanes to immutable
  tuples and freeze nested candidate/detail payloads at construction, keeping
  frontend-local target evidence stable after emission.
- UK replay recovery action/target details now attach a nested shared
  target-resolution certificate when the recovery explicitly names an alternate
  `recovery_target`, avoiding fabricated target evidence for recoveries that
  only repair source shape in place.
- New Zealand inline text occurrence checks now share one frontend-local rule
  set backed by core comparison-normalization validation, replacing duplicate
  ad hoc whitespace and punctuation normalization in instruction-workqueue and
  effect-candidate surfaces.
- Estonia parse-time target-title rejection diagnostics now carry a nested
  shared target-resolution rejection certificate while preserving EE-local
  title and statute-fragment payloads.
- Estonia instruction-waist `exclude_paths` now uses shared tree-path aliases
  while keeping Estonia-local numeric and label range tuples local.
- source-version bracketing now has shared diagnostic projections for
  date-window and change-window evidence; New Zealand effect-candidate rows
  nest date-window and source-change-window projections beside existing flat
  fields so source-only/non-effectivity truth claims are queryable.
- source-version bracketing now validates requested source dates and projection
  contracts: invalid requested dates are rejected instead of selecting a
  misleading witness window, source-window diagnostics must retain their
  source-only truth claim, and `replay_claims` must remain false.
- lossless filter carriers now require rejected items to carry a non-empty
  reason and boolean blocking flag, and direct `FilterResult` construction
  normalizes accepted/rejected lanes to immutable tuples.
- shared processing artifact status now validates the complete/partial/blocked/
  failed vocabulary, forbids blockers on complete artifacts, requires blockers
  on degraded artifacts, and compile-facade wire export falls back to the
  verdict status when a blocking verdict has no barrier codes.
- shared verification report contracts now reject missing issue/divergence
  identifiers, invalid severity values, out-of-range divergence scores,
  filtered divergences without rule/reason evidence, and negative summary or
  coverage counts.
- shared verification report contracts now freeze nested issue/divergence/
  coverage/summary detail payloads and normalize issue/divergence lanes at
  construction.
- shared corpus evidence rows now run their existing row validators at
  construction time, so invalid operation/finding evidence cannot bypass the
  envelope contract by skipping explicit validator calls.
- shared corpus evidence rows and summaries now freeze nested detail/evidence
  payloads and normalize tuple-like ID/category lanes at construction.
- shared replay summary/checkpoint contracts now validate required IDs, status
  fields, non-negative counts, replay text-view shape, and checkpoint bounds at
  construction time.
- shared replay step/summary contracts now normalize step lanes and freeze
  nested detail payloads at construction, keeping report details stable after
  emission.
- branch/proposal impact projections now validate their UI/API envelope at
  construction time: rows require a status and mapping detail, and projections
  require a real `LegalBranch`, status, row records, and mapping detail.
- branch/proposal impact projections now also normalize row lanes and freeze
  nested row/projection detail payloads at construction.
- PIT materialization now has a shared `degraded_timeline_issues` status for
  rendered statutes with blocking timeline diagnostics. Facade materialization
  uses it when timeline compilation emits blocking issues, including
  unresolved contingent temporal events that were not applied.
- temporal-resolution evidence now validates its shared status/family
  vocabulary at construction, and `certified_untriggered` evidence must carry
  a source locator, authority layer, or trigger-coverage certificate.
- temporal-resolution evidence now freezes nested detail payloads at
  construction, so trigger-coverage certificates and other frontend-local
  temporal witnesses cannot be mutated after the evidence object is created.
- `PhaseResult` runtime finding carriers now freeze nested detail payloads at
  construction for observations, obligations, violations, and projected
  findings, so stage side-channel evidence cannot be rewritten after emission.
- source-pathology and trigger-coverage certificate carriers now freeze nested
  detail payloads at construction; trigger coverage also normalizes source
  lanes to tuples, keeping temporal/source evidence stable after emission.
- frontend replay adjudication projections and typed timeline invariant
  violations now freeze nested evidence/detail payloads at construction, so
  replay/oracle and timeline diagnostic rows stay stable after emission.
- capture source-pathology views and New Zealand acquisition diagnostics now
  freeze nested detail/metadata/source-lane payloads at construction, keeping
  report-facing source evidence stable after emission.
- invariant detector result rows now validate required identity/message fields
  and freeze nested detail payloads at construction, so typed detector evidence
  cannot be rewritten after message projection.
- temporal scope and event carriers now validate executable event identity,
  supported event kind, scope/source/activation-rule types, and tuple-normalize
  address/predicate lanes at construction, preventing malformed temporal facts
  from entering timeline replay by type-hint accident.
- provenance expiry and migration carriers now normalize immutable lanes and
  validate migration identity, migration kind, and endpoint address types at
  construction, so lineage/expiry evidence cannot be malformed or mutated by
  caller-owned input collections.
- parse witness and resolution witness carriers now normalize token spans,
  validate nested resolution witness type, and freeze resolution context payloads
  at construction, keeping parser evidence stable after emission.
- coverage report carriers now validate claim-kind and gap-disposition
  vocabularies, normalize coverage/evidence lanes, and reject malformed report
  rows at construction, so uncovered-body classifications cannot disappear by
  falling outside known buckets.
- typed table surface carriers now validate row-key basis/strength vocabularies,
  normalize row/cell lanes, and reject malformed row/body records, keeping
  table-target evidence stable after projection from IR or oracle XML.
- version-selection certificate/result carriers now validate status and rail
  vocabularies, candidate counts, selected-version agreement, and missing-scope
  obligations at construction, so PIT queries cannot emit contradictory
  selection evidence.
- unit-registry publication now validates unit specs, freezes the unit-kind
  mapping, normalizes facet vocabularies, and rejects mismatched registry keys,
  so lowering targets cannot depend on caller-owned mutable registry state.
- timeline/materialization result carriers now validate issue kinds, degradation
  status obligations, certificate/result count agreement, and freeze timeline
  mapping keys at emission time, keeping PIT result evidence internally
  consistent.
- shared replay and verification summaries now reject non-integer counters,
  incompatible consistency/divergence rows, and obvious emitted-row count drift,
  while preserving zero-count defaults for legacy callers that omit summary
  counts.
- materialization lineage plan/decision carriers now validate plan modes,
  timeline sources, migration-event lanes, and freeze the chosen timeline input
  mapping, so raw-vs-rekeyed PIT decisions cannot be mutated after selection.
- parse-layer effect-intent carriers now validate their fixed discriminants,
  optional date payloads, contingent flags, and raw-text fields at construction,
  preventing malformed temporal/applicability meaning from reaching temporal
  lowering by type-hint accident.
- chain-completeness blockers now validate their reason-code vocabulary,
  section/scope identifiers, source-statute type, and blocker lanes at
  construction, protecting negative-proof evidence from malformed blocker rows.
- resolved target-scope carriers now reject non-neutral unit kinds and
  non-string scope fields when constructed directly, keeping shared proof/report
  scope evidence inside the core section/chapter/part contract.
- UK REPLACE-as-INSERT recovery (§1.2 action-family conversion) now emits
  `uk_replay_replace_materialized_as_insert_for_missing_leaf`
  (family=target_resolution_recovery, blocking=False, strict_disposition=block,
  quirks_disposition=apply) when a REPLACE op falls through to _insert_node_v2
  because the target leaf is absent but the replacement payload matches the
  missing leaf kind and label exactly; rule ID registered in
  `UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS` in source_adjudication.py; witness
  fields: leaf_kind, parent_path, payload_kind, payload_label.
- UK word-to-structural substitution escalation (§1.2 action-family conversion)
  now emits `uk_effect_word_substitution_escalated_to_structural_replace`
  (family=action_family_recovery, blocking=False, strict_disposition=block,
  quirks_disposition=apply) via `_append_uk_effect_lowering_observation` in
  effect_text_fragment_lowering.py when a "substituted for words" effect feed
  row carries a structural payload whose kind+label match the target leaf; the
  lowering still proceeds to curr_action="replace" in quirks mode; rule ID
  registered in `UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS` in
  source_adjudication.py; witness fields: source_payload_kind,
  source_payload_label, target_leaf_kind, target_leaf_label.
- UK payload label realignment (§1.3/§1.5 payload mutation ownership) now emits
  `uk_effect_payload_label_realigned_to_target_leaf`
  (family=payload_realignment, blocking=False, strict_disposition=block,
  quirks_disposition=apply) via `_append_uk_effect_lowering_observation` in
  `effect_payload_normalization.py:prepare_uk_operation_payload_node` when an
  insert payload has a blank label but its kind matches the canonical target leaf
  kind; the label is realigned to the target leaf label (mutation still proceeds
  in quirks mode); rule ID
  `_UK_EFFECT_PAYLOAD_LABEL_REALIGNED_TO_TARGET_LEAF_RULE_ID` defined in
  `effect_payload_normalization.py` and registered in
  `UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS` in `source_adjudication.py`; witness
  fields: original_payload_label, new_payload_label, payload_kind,
  target_leaf_kind, target_leaf_label; covered by
  tests/test_uk_effect_payload_realignment.py (tests 1.1–1.6: positive, result,
  negative-non-blank, strict-disposition, witness-fields, family).
- UK payload kind realignment (§1.3/§1.5 payload mutation ownership) now emits
  `uk_effect_payload_kind_realigned_to_target_leaf`
  (family=payload_realignment, blocking=False, strict_disposition=block,
  quirks_disposition=apply) via `_append_uk_effect_lowering_observation` in
  `effect_payload_normalization.py:prepare_uk_operation_payload_node` when an
  insert payload has a leafish kind that differs from the canonical target leaf
  kind but whose label-number matches the target leaf label; the kind is realigned
  to the canonical target leaf kind (mutation still proceeds in quirks mode); rule
  ID `_UK_EFFECT_PAYLOAD_KIND_REALIGNED_TO_TARGET_LEAF_RULE_ID` defined in
  `effect_payload_normalization.py` and registered in
  `UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS` in `source_adjudication.py`; witness
  fields: original_payload_kind, new_payload_kind, payload_label, target_leaf_kind,
  target_leaf_label; the existing test
  `test_prepare_uk_operation_payload_node_canonicalizes_point_leaf_kind` in
  `test_uk_replay_adjudications.py` was updated to assert the observation fires
  for subparagraph→item realignment (previously asserting empty lowering_rejections);
  covered by tests/test_uk_effect_payload_realignment.py (tests 2.1–2.6: positive,
  result, negative-matches, strict-disposition, witness-fields, family) plus a
  cross-site negative for replace action.
- UK fee-target refinement failure (§1.10 narrow try-except) now emits
  `uk_effect_fee_target_refinement_failed` (family=lowering_rejection,
  blocking=False, strict_disposition=block, quirks_disposition=apply) when the
  compile-time fee-target refinement loop in `effect_compiler.py` catches a
  `ValueError` from `_parse_affected_target`, `canonicalize_uk_address`, or
  `_uk_table_driven_fee_target_refinements`; the original `t_str` is still
  appended (fallback preserved); the broad `except Exception` has been narrowed
  to `except ValueError`, so unanticipated exception types (e.g. RuntimeError)
  propagate; rule ID `_UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID` defined
  in `effect_compiler.py` and registered in `UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS`
  in source_adjudication.py; witness fields: input_t_str, failed_helper,
  exc_message; covered by tests/test_uk_effect_fee_target_refinement.py
  (5 tests: positive, witness fields, strict-disposition, negative-valid-parse,
  RuntimeError-propagates).

- boolean text classifiers in `source_adjudication.py` (like `_looks_like_referent_qualified_text_substitution`) follow the bounded-regex + fast-guard pattern per §1.11: three substring guards eliminate the regex path for non-matching inputs, and `.{0,500}` quantifiers replace unbounded `.+` to prevent O(N^3) backtracking; module-scope `re.compile` with `chr()` for non-ASCII quote chars keeps the constant definition ASCII-safe.
- UK fee-table rows are now indexed once per source_root via `_uk_get_fee_table_index` (§1.11 pattern: structural data walked per effect → walk once and index); a `WeakKeyDictionary` cache keyed on the source_root `ET.Element` holds the pre-built `_UKFeeTableIndexEntry` tuples — including pre-lowercased `col1_lower` — for the lifetime of that source_root, eliminating the 132,140 repeated `source_root.iter()` + `_uk_table_rows_with_rowspans` calls that drove 145M+ str.split/str.lower ops and 2.5 GB RSS on ukpga/1970/9.
- Actuator 8 bounded-regex + fast-guard pattern applied in bulk (Sensor H #12–15) to FI/SE/tools landmines: `citation_routing._looks_like_fi_meta_repeal` (also re-used in `grafter.py`), `clause_patterns._MIXED_ROW_PATTERNS` and `_SINGLE_ROW_{REPLACE,REPEAL}_RE`, `sweden/grafter._SE_{REPLACE,REPEAL,RENUMBER}_CLAUSE_RE` and `_SE_WORD_SUBSTITUTION_RE`, and `divergence_heuristics._REPEAL_PRIOR_WORDING_BANNER_RE`/`_FUTURE_REPEAL_OVERLAY_RE` — all inline/unbounded quantifiers replaced with `{0,200–500}?` bounds and module-scope compiles, matching `f2ee4479` template.
- UK extraction context (parent_map + exact_id_map + sequence_map) is now built once per root ET.Element via `_build_extraction_context` using a `WeakKeyDictionary` cache (§1.11 pattern, mirroring Actuator 9's fee-table index in `table_sources.py`); eliminates redundant tree-walks when the same root is passed multiple times in non-compile-session code paths (e.g. tools/CLI) while adding negligible overhead for the already-deduplicated compile path.
- UK source-root lifecycle (§source_root_lifecycle): `compile_ops_for_statute` now evicts affecting-act source contexts after each act's last effect in the ordered sequence. Eviction covers extraction_cache, enacted_extraction_cache, and three module-level WeakKeyDictionary caches (_source_parent_map_cache, _source_ancestor_chain_cache in source_context.py; _EXTRACTION_CONTEXT_CACHE in provision_extractor.py). Explicit eviction is required because these caches create reference cycles (parent_map values include root as a parent element; ancestor-chain tuples include root as terminal ancestor) that prevent Python reference-count GC without explicit help. WSL2 constraint: 96 GB physical RAM but WSL2 hangs under high memory pressure; peak RSS for ukpga/1970/9 was 2.5–2.6 GB (229 unique affecting-act roots × ~400x in-memory ET tree expansion). After fix: rss_mb=857 — 67% reduction from ~2600 MB. Adjudication parity confirmed (score=40.8%, replay=80.0%, ops=1988, effect_rows=3265). Acts with non-contiguous occurrence in the ordered sequence (40 out of 229 for ukpga/1970/9) are transparently re-parsed from archive bytes on re-access. Pattern: try/finally in the compile loop fires eviction on both continue and fall-through paths. Covered by tests/test_uk_source_root_lifecycle.py (8 tests: parent-map release, ancestor-chain release, parent-map correctness, ancestor-chain correctness, None-inputs, eviction-index construction, cache-hit identity, cache isolation).

## Ranked Promotion Candidates

### P0. Source-Lane Selection Evidence

Problem:

- acquisition code increasingly chooses between multiple source lanes:
  current XML, enacted XML, official PDF, legacy PDF guesses, archive members,
  and duplicate logical locators;
- those choices are evidence-sensitive, but the record shape is still local to
  each frontend.

Evidence:

- `src/lawvm/uk_legislation/source_context.py::_select_enacted_source_for_current_shell`
- `src/lawvm/uk_legislation/source_state.py::uk_affecting_act_current_shell_enacted_source_selected`
- `src/lawvm/sweden/fetch.py::fetch_se_official_artifacts`
- `src/lawvm/norway/index.py::_deduplicated_no_amendment_artifacts`

Promotion:

- status: initial evidence carrier exists in `src/lawvm/core/source_lane.py`;
- it records source-lane attempts, selected lane, locator, local attempt
  details, and blocking disposition;
- lane policy remains local to each frontend;
- current users include Finland operative-text acquisition, Sweden official-PDF
  fallback acquisition, Norway duplicate logical locator diagnostics, UK
  affecting-source selection, Estonia RT XML/feed acquisition diagnostics, EU
  Cellar manifest request failures, and New Zealand latest-XML locator
  candidate/HTTP acquisition rejections.

Why high value:

- makes acquisition-layer source choice auditable across jurisdictions;
- prevents future frontends from treating fallback source lanes as invisible
  transport details;
- gives strict mode a common way to reject ambiguous or non-authoritative lane
  selection while quirks mode can still proceed with evidence.

Risk:

- do not encode jurisdiction authority policy in core;
- do not imply a selected source lane is legally authoritative merely because
  it was selected;
- start as an evidence carrier before replacing existing diagnostics.

Tests needed:

- UK current-shell to enacted-source selection preserves current and enacted
  locators, sizes, and previews;
- Sweden fallback PDF lane emits nonblocking source-lane selection evidence;
- Norway duplicate byte-identical artifacts select a deterministic witness but
  conflicting duplicates block;
- malformed records must reject missing selected/rejected lane reason fields.

### P0. Source-Version Bracketing Witness

Problem:

- Finland, New Zealand, and Estonia each choose exact/latest/on-or-before
  source versions from ordered official artifacts;
- the shared invariant is a witness-window selection, not the jurisdiction's
  legal temporal semantics.

Evidence:

- `src/lawvm/finland/consolidated_artifacts.py`
- `src/lawvm/new_zealand/version_diff.py`
- `src/lawvm/estonia/fetch.py`

Promotion:

- status: shared source-version date/change window selectors and diagnostic
  projections exist in `src/lawvm/core/source_version_window.py`;
- the shared surface records requested source date, surrounding witness
  versions, truth-claim names, and explicit `replay_claims=False`;
- New Zealand archived XML version-diff and effect-candidate evidence rows now
  project date and source-change windows through the shared carrier;
- jurisdiction authority, commencement interpretation, and oracle truth remain
  outside the helper.

Why high value:

- makes oracle/source bracketing auditable for replay-vs-oracle checks;
- reduces hidden date-window policy drift between frontends.

Risk:

- must be clearly named as source witness selection, not proof of legal
  effectivity.

Tests needed:

- exact match, on-or-before, strict-before, on-or-after, latest, no candidate,
  duplicate-date tie-break, invalid requested date rejection, and
  no-authority-claim metadata.

### P1. Target Resolution Certificate

Problem:

- Finland sparse-slot binding and UK target refinement both compute candidate
  counts, ambiguity, fallback status, selected target, and rule evidence;
- current records are local and hard to query uniformly.

Evidence:

- `src/lawvm/finland/payload_normalize.py`
- `src/lawvm/uk_legislation/replay_target_lookup.py`
- `src/lawvm/uk_legislation/effect_target_prelude.py`

Promotion:

- status: initial evidence carrier exists in
  `src/lawvm/core/target_resolution.py`;
- it includes source target, candidate count, selected target, listed
  candidates, status, rule ID, confidence, and strict/quirks disposition;
- keep resolver logic and local target grammar in frontends.

Why high value:

- directly supports the no-target-hijacking invariant;
- lets tools ask where fallback or ambiguity entered the pipeline.

Risk:

- avoid forcing Finland sparse-slot and UK effect-target vocabularies into one
  enum too early.

Tests needed:

- zero candidate, single candidate, ambiguous candidates, fallback candidate,
  selected target differs from metadata target, and strict mode blocks fallback
  while still emitting the certificate.

### P1. Temporal Resolution Status Contract

Problem:

- UK commencement overrides, Norway effective-date statuses, and Finland
  activation-rule lowering all classify temporal evidence;
- the language-specific date extraction is local, but the evidence status
  shape is reusable.

Evidence:

- `src/lawvm/uk_legislation/effect_temporal.py`
- `src/lawvm/norway/sources.py`
- `src/lawvm/finland/temporal_lowering.py`

Promotion:

- status: partial first pass exists in
  `src/lawvm/core/temporal_resolution.py`;
- current shared statuses cover fixed, immediate, source-backed override,
  unresolved contingent, unknown effective date, and future effective date;
- include locator/source text when a recovery or override is used;
- still not promoted: language-specific date extraction, commencement
  doctrine, and ambiguous multi-date adjudication policy.

Why high value:

- temporal uncertainty is a cross-jurisdiction replay blocker;
- shared evidence would make strict-mode temporal barriers easier to reason
  about.

Risk:

- do not promote language-specific effective-date parsers or commencement
  doctrines.

Tests needed:

- fixed date, immediate, contingent/pending decree, unknown/missing,
  source-backed override, ambiguous multi-date without override, and strict
  rejection of unproven temporal recovery.

### P0. Typed Tree Invariant Violations

Problem:

- `check_invariants` still exposes strings for compatibility;
- tests and scripts parse or reconstruct duplicate/order cases independently.

Implemented progress:

- core now has `TreeInvariantViolation` and
  `iter_tree_invariant_violations(...)`;
- UK replay duplicate/order scanning consumes the typed iterator while
  preserving legacy rendered adjudication details;
- `diagnose-phase` consumes typed tree invariant records for
  `duplicate_label`, `illegal_edge`, and `all_tree` detectors;
- UK replay shape-gap classifiers can consume typed invariant records instead
  of reparsing `"duplicate ..."` / `"out of order ..."` messages.
- EU replay invariant adjudications consume typed duplicate/order invariant
  records while preserving the legacy `violation` string in evidence details.
- Norway replay invariant adjudications consume typed duplicate/order invariant
  records while preserving their legacy joined `violations` string.
- Sweden replay now emits typed invariant metadata beside the existing string
  list; Sweden's expected-shape tolerances are keyed from typed
  `unexpected_child_kind` fields rather than substring matches.
- core now has a small typed detector-result adapter that preserves legacy
  detector messages while giving tools a typed `InvariantDetectorResult`
  surface.

Evidence:

- `src/lawvm/core/tree_ops.py::check_invariants`
- `src/lawvm/uk_legislation/replay_invariant_diagnostics.py::_collect_duplicate_order_invariants`
- `src/lawvm/uk_legislation/replay_target_gaps.py::uk_payload_shape_invariant_violations`
- `src/lawvm/tools/diagnose_phase.py`
- `scripts/audit_invariants.py::_classify_violation`
- `tests/test_materialization_invariants.py::_find_duplicates`

Promotion:

- add a core `TreeInvariantViolation` record with fields like
  `kind`, `path`, `message`, `parent_kind`, `child_kind`, `label`,
  `normalized_label`, `count`, `previous_label`, `next_label`;
- add `iter_tree_invariant_violations(tree, *, sort_key=None, families=None,
  root_path=None)`;
- keep the current string output as a projection wrapper for compatibility.

Why high value:

- lets UK reuse generic duplicate/order invariant detection while preserving
  adjudication-specific classification;
- stops downstream tooling from matching text like `"duplicate"` or
  `"out of order"`;
- creates a stable evidence surface for invariant-to-claim logic.

Risk:

- path formatting must preserve current strings during migration;
- UK needs a way to scan only duplicate/order families cheaply;
- first promotion must not change strict-mode behavior.

Compatibility path:

1. add typed records and core tests while preserving exact
   `check_invariants(...) -> list[str]` output; done.
2. migrate UK replay duplicate/order diagnostics to the typed iterator while
   preserving returned strings and adjudication kinds; done for replay
   invariant-gap classification.
3. migrate `diagnose-phase`, `audit_invariants.py`, and materialization tests
   away from regex/string reconstruction after the typed records are stable;
   `diagnose-phase` is done for tree invariant detectors, Sweden/Finland replay
   metadata expose typed records, and `audit_invariants.py` now consumes typed
   replay metadata with a legacy-string fallback.

### P0. Generic Mutation Event Accounting

Problem:

- Finland has rich apply mutation events;
- Open Law uses snapshot diff reports;
- UK currently has replay adjudications and duplicate/order scans, but no cheap
  per-op changed-path event surface.

Evidence:

- `src/lawvm/finland/apply_events.py`
- `src/lawvm/open_law/audit.py`
- `src/lawvm/uk_legislation/replay_executor.py`
- `src/lawvm/core/mutation_boundary.py`

Promotion:

- define a core `MutationEvent` / `MutationEventReport` parallel to
  `MutationBoundaryReport`;
- include `op_id`, `action`, `outcome`, `target_path`, `parent_path`,
  `changed_paths`, `declared_recovery_paths`, `declared_migration_paths`;
- expose a helper that partitions event-touched paths through
  `operation_storage_boundary_prefixes`.

Implemented progress:

- core already owns `MutationEvent` and `DeclaredMutationAllowance`;
- core now owns `MutationEventPathSetReport` plus helpers for touched paths,
  allowance paths/rule IDs, matched allowance rule IDs, and path-set
  partitioning; the path-set partition invariant is now property-tested across
  arbitrary touched, target, recovery, and migration path sets.
- core now owns `MutationAccountingResult` and `MutationInvariantReport` plus
  the passive accounting analyzers for skipped/failed mutation, missing primary
  target consumption, unresolved apply boundary, and out-of-target touches.
- Finland apply accounting now delegates target/recovery/migration path-set
  classification to the core report while preserving its frontend-specific
  accounting result codes and compatibility report fields.
- UK replay emits opt-in core mutation events for central node
  replace/remove/insert helpers, whole-act repeal, direct table/schedule-table
  children-splice helpers, and schedule-list entry insert/repeal/replace
  helpers. Fallback schedule-root insertion now records the same supplement
  insertion event as ordinary supplement insertion. Same-parent sibling
  renumber emits `renumbered_paths`. Table row splices intentionally record the
  table container as the changed path because row identity may be unlabeled and
  non-unique in source XML.

Why high value:

- UK can emit path events from replay helpers without expensive whole-tree
  snapshots;
- Finland can gradually project its event schema to the core carrier;
- evidence code can consume one mutation-boundary shape across jurisdictions.

Risk:

- do not force UK to snapshot whole IR after each op;
- event paths must be recorded at mutation sites while the helper still knows
  what changed.

### P1. Shared Comparison Normalization Rule Engine

Problem:

- Estonia has a named comparison-normalization rule pipeline;
- Open Law has local typography projection;
- UK has local comparison/effect normalization helpers;
- Finland has multiple whitespace and dash normalization utilities.

Evidence:

- `src/lawvm/estonia/compare.py::_EE_NORMALIZATION_RULES`
- `src/lawvm/open_law/audit.py::_project_typography_for_snapshot_compare`
- `src/lawvm/uk_legislation/source_adjudication.py::_normalize_effect_text`
- `src/lawvm/finland/corrigendum.py::_normalize_ws`

Promotion:

- add core `ComparisonNormalizationRule` and `NormalizationPipeline`;
- support regex, literal replacement, translation-table, and placeholder rules;
- emit rule IDs/classes for evidence where needed.

Why high value:

- prevents long-tail whitespace, dash, quote, and entity logic from being
  reimplemented in each frontend;
- makes oracle/display projection auditable instead of just "normalized";
- helps distinguish presentation cleanup from legal text mutation.

Risk:

- only promote comparison/oracle projection mechanics;
- keep jurisdiction-specific legal morphology and source parser rules local;
- do not reuse `tree_ops.normalize_text` for comparison projection without a
  dedicated projection/finding wrapper.

Low-risk first migration:

- migrate only Open Law curly/straight quote comparison projection first;
- keep Open Law annotation dropping local, because `hcontainer:annos` is
  Open Law/Maryland snapshot semantics;
- later adapt Estonia's local rule dataclass to the core carrier while leaving
  `_EE_NORMALIZATION_RULES` local.
- Sweden comparison text normalization now uses the core rule carrier for dash
  equivalence, editorial attribution suffixes, leading section numbers, and
  inline list numbering.
- Norway comparison text normalization now uses the core rule carrier for
  bounded whitespace, punctuation, footnote-marker, numeric hyphen, and
  other-laws placeholder presentation cleanup.
- Finland oracle comparison text normalization now uses the core rule carrier
  for the shared Finlex presentation residue cleanup used by bench and oracle
  check paths.

### P1. Diagnostic Envelope / Disposition Builder

Problem:

- many frontends manually build dicts with `rule_id`, `blocking`,
  `strict_disposition`, and `quirks_disposition`;
- `CompileAdjudication`, `Finding`, and frontend-local finding classes overlap
  but are not one projection surface;
- current adapters sometimes infer missing `blocking` policy implicitly.

Evidence:

- `src/lawvm/replay_adjudication.py::CompileAdjudication`
- `src/lawvm/core/phase_result.py::Finding`
- `src/lawvm/open_law/models.py::OpenLawFinding`
- `src/lawvm/uk_legislation/lowering_records.py`
- `src/lawvm/uk_legislation/replay_records.py`
- `src/lawvm/sweden/fetch.py`
- `src/lawvm/norway/index.py`
- `src/lawvm/norway/sources.py`
- `src/lawvm/estonia/fetch.py`
- `src/lawvm/estonia/pair_planning.py`
- `src/lawvm/core/adjudication_evidence.py`

Promotion:

- use the existing small core `diagnostic_records.py` helper for envelope
  detail dicts;
- possible future records: `DiagnosticDisposition` and `DiagnosticEnvelope`;
- standardize only `rule_id`, `family`, `phase`, `reason/message`,
  `blocking`, `strict_disposition`, `quirks_disposition`, optional source/op
  IDs, and `detail`;
- keep frontend-local finding classes, but make wire fields consistent.

Implemented progress:

- `diagnostic_detail(...)` exists in core and is covered by tests;
- UK lowering records build their base details through `diagnostic_detail`;
- the central UK replay action/target detail builder now delegates envelope
  defaults to `diagnostic_detail` while letting `_build_uk_replay_adjudication`
  attach the final replay rule ID.
- Norway replay skip/adjudication helpers and Estonia orchestration/unsupported
  replay adjudication helpers now delegate their shared envelope defaults to
  `diagnostic_detail` while preserving frontend-local adjudication carriers.
- Norway amendment-index acquisition and parser-adjudication diagnostics now
  delegate their shared envelope defaults to `diagnostic_detail`.
- Norway source-loader and public-archive ingest diagnostics now delegate their
  shared envelope defaults to `diagnostic_detail`.
- Norway inventory current-law fallback diagnostics, malformed structured
  renumber-token parse diagnostics, and replay action-family recovery
  diagnostics now also delegate their shared envelope defaults to
  `diagnostic_detail`.
- Norway unstructured parse adjudications, structured target/action recovery
  adjudications, structured document-change base failures, unresolved
  structured target skips, cross-base structured target skips, and structured
  renumber skip diagnostics now delegate their shared envelope defaults to
  `diagnostic_detail`.
- Estonia RT acquisition diagnostic record projections now delegate their
  shared envelope fields to `diagnostic_detail`.
- Estonia grafter source-local global text-replace selector exclusions and
  old-format reference-slice filter adjudications now delegate their shared
  envelope fields to `diagnostic_detail`.
- Sweden replay adjudication helpers now delegate the same replay envelope
  defaults to `diagnostic_detail`.
- Sweden acquisition diagnostics for official artifacts and RK current JSON now
  delegate source-pathology envelope defaults to `diagnostic_detail`.
- Sweden scraped-document ingest diagnostics, later-chain reverse replay
  exceptions, and official rebuild-chain status diagnostics now also delegate
  their shared envelope defaults to `diagnostic_detail`.
- Sweden official-act grafter diagnostics for renumber arity, payload rows,
  amendment-register rows, current-text extraction, effect lowering skips,
  effect-plan adjudications, and unclaimed payloads now delegate their shared
  envelope defaults to `diagnostic_detail`.
- EU pipeline diagnostics and replay adjudication helpers now delegate their
  shared envelope defaults to `diagnostic_detail`.
- EU apply-step text-duplication replay warnings now also delegate their
  shared nonblocking envelope defaults to `diagnostic_detail`.
- EU parser extraction diagnostics now delegate their shared envelope defaults
  to `diagnostic_detail`.
- EU Cellar acquisition diagnostics and REUL bridge target-resolution
  diagnostics now delegate their shared envelope fields to `diagnostic_detail`.
- replay adjudication evidence projection now has shared adapters
  `adjudication_diagnostic_detail(...)` and
  `adjudication_record_diagnostic_detail(...)`, so object and persisted-record
  adjudications get the same normalized diagnostic envelope in evidence rows.
- UK replay-adjudication candidate rows and UK prepare-filter rejection
  projections now use the shared replay-adjudication envelope adapters where
  they need outward JSON/reporting details.
- UK manual-frontier validation rows now use the shared diagnostic envelope
  helper for normal, input-error, and effect-not-found validation results while
  keeping the validator row schema stable.
- UK effect-feed count diagnostics in evidence bundles and Norway Statsrad
  diagnostics now delegate their shared envelope fields to
  `diagnostic_detail`.
- Open Law corpus finding evidence rows now derive their shared disposition
  envelope fields through `diagnostic_detail`.
- New Zealand acquisition diagnostics, latest-XML locator rejection diagnostics,
  benchmark findings, and operation-surface findings now delegate their shared
  envelope fields to `diagnostic_detail` while preserving NZ-local warning and
  witness-only dispositions.

Why high value:

- reduces silent inconsistencies in strict/quirks semantics;
- improves evidence aggregation across newer frontends.

Risk:

- do not collapse source pathology, replay adjudication, and evidence claim
  layers into one semantic type;
- do not collapse this into `PhaseResult.Finding` yet; that type is
  registry-governed internal phase state, while many frontend diagnostics are
  intentionally local and unregistered;
- do not merge `SourceAdjudication` and `CompileAdjudication`.

### P1. Public Label And Path Utility Surface

Status:

- public wrappers now exist in `src/lawvm/core/tree_ops.py`:
  `normalized_label_key` and `default_label_sort_key`;
- frontend/tool call sites no longer import or call the private
  `_norm` / `_default_sort_key` helpers directly.

Original problem:

- frontends call private core helpers like `_default_sort_key` and `_norm`;
- each frontend/tool has local path dedupe and prefix helpers.

Evidence:

- `src/lawvm/core/tree_ops.py::_default_sort_key`
- `src/lawvm/core/tree_ops.py::default_label_sort_key`
- `src/lawvm/core/tree_ops.py::normalized_label_key`
- `src/lawvm/estonia/grafter.py`
- `src/lawvm/core/mutation_boundary.py`

Promotion:

- expose public wrappers like `default_label_sort_key`,
  `normalized_label_key`, and `dedupe_tree_paths`;
- keep old private names as compatibility internals.

Why high value:

- makes reuse explicit and type-checkable;
- reduces private-import coupling before stricter ty work.

Risk:

- default sort key is not legally universal; call it default/core, not
  jurisdiction-canonical.

### P2. Phase-Local Invariant Attribution For UK / Non-Finland Frontends

Problem:

- `diagnose-phase` and `invariant-bisect` are valuable but Finland-chain
  oriented;
- UK has bench/candidate adjudications but less generic phase-local invariant
  blame tooling.

Evidence:

- `src/lawvm/tools/diagnose_phase.py`
- `src/lawvm/tools/invariant_bisect.py`
- `src/lawvm/uk_legislation/replay_invariant_diagnostics.py`
- `notes/JURISDICTION_CLI_TOOLING_CONTRACT.md`

Promotion:

- define a frontend-neutral invariant checkpoint protocol;
- expose phase snapshots or event streams through jurisdiction adapters;
- let `invariant-bisect` operate on any frontend that implements the protocol.

Why high value:

- prevents UK fixes from chasing final-output symptoms;
- makes future jurisdictions less dependent on bespoke CLI tooling.

Risk:

- higher integration cost; do after typed invariant records and mutation
  events exist.

### P2. Source Pathology / Adjudication Projection Adapter

Problem:

- source pathology, replay adjudication, and finding rows use compatible fields
  but different carriers;
- evidence code has to know too many frontend projection shapes.

Evidence:

- `notes/SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md`
- `src/lawvm/core/phase_result.py`
- `src/lawvm/replay_adjudication.py`
- `src/lawvm/open_law/corpus_audit.py`
- `src/lawvm/uk_legislation/source_adjudication.py`

Promotion:

- add projection adapters to core evidence contracts, not one mega-record;
- require `rule_id`, `phase`, `family`, `blocking`, `strict_disposition`,
  `quirks_disposition` on persisted rows.

Why high value:

- improves cross-jurisdiction evidence indexing;
- keeps phase-local semantics while normalizing the outward row surface.

Risk:

- avoid erasing the distinction between source defect, compiler adjudication,
  replay violation, and oracle adjudication.
- Capture replay-meta, amendment, and payload report rows now freeze nested
  row/count payloads at construction time. The JSON-facing `to_dict()` surface
  remains compatibility-oriented, but in-process captures can no longer be
  mutated after emission by downstream debug/report code.
- `FrozenDict` now rejects in-place union (`|=`), closing the remaining dict
  mutator path for supposedly frozen core attrs, metadata, and evidence
  detail mappings.
- mutation-boundary report carriers now normalize and validate directly
  constructed path collections, while preserving changed-path multiplicity for
  total partition accounting.
- divergence partitions now normalize primary and filtered divergence rows to
  tuples and reject malformed filtered records, preventing post-partition
  mutation of verification evidence lanes.
- timeline lineage bridge carriers now validate address/event and boolean flag
  payloads at construction time, preventing stringly or non-boolean branch
  classifications from entering PIT lineage planning.
- compile contract carriers now validate strict-profile booleans, source
  completeness counts, compiled-op provenance/scope witnesses, admissible
  binding certificates, canonical effect family/target shape, and strict
  verdict barrier contradictions at construction time.
- mutation-accounting report carriers now validate result-code vocabulary,
  path collections, rule-id collections, result record types, and invariant
  booleans at direct construction time.
- finding-registry specs now validate family, default enforcement, proof
  categories, registry role, owner, and description at construction time.
- `DuplicateChildFinding` now validates classification vocabulary against
  `_VALID_DUPLICATE_CHILD_CLASSIFICATIONS`, non-empty `child_kind`/`child_label`/
  `reason`, and `child_count >= 2` at `__post_init__`; `timeline_issue_kind_for_duplicate_classification`
  raises `ValueError` (not `KeyError`) for unknown classifications, listing
  supported values.

## Keep Local For Now

Do not promote yet:

- Finnish clause grammar and sparse payload binding rules;
- Estonian morphology-specific replacement generation;
- UK effect-feed target-shape parsing and manual frontier classifiers;
- Open Law XML/codify namespace parsing;
- Sweden/Norway acquisition-specific source lane decisions.

These are jurisdiction source semantics, not shared invariants.

- UK recursive-descent target recovery is now gated on uniqueness.
  `uk_replay_target_resolved_by_recursive_descent` (family=target_resolution_recovery,
  blocking=False, strict_disposition=block, quirks_disposition=apply) is emitted when
  exactly one deeper descendant matches the expected kind/label after direct path fails.
  `uk_replay_target_ambiguous_recursive_descent` (family=target_resolution_recovery,
  blocking=True) is emitted and no target is selected when multiple descendants match.
  Both rule IDs are owned in UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS and
  UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS respectively. The fix is family-level
  (§1.1 target_resolution_recovery), gating all target lookup paths that previously
  accepted `curr_cands[0]` without uniqueness checking.  The recursive descent walk is
  short-circuited at ≥2 matches (callers only need 0/1/≥2 to decide) and the per-node
  all-matches result is cached in `_recursive_match_all_cache` keyed by (id(node), kind,
  label), invalidated on any tree mutation via `_note_structure_mutation`.

## Recommended Next Work

1. Continue replacing local path tuple spellings with shared aliases such as
   `TreePath` / `TreePaths` where that reduces type noise without changing
   serialized evidence. The shared aliases now live in core; remaining work is
   opportunistic call-site cleanup rather than semantic promotion.
2. Extend UK mutation-event emission to any remaining direct structural
   mutation helpers not routed through central replace/remove/insert or the
   table/schedule children-splice recorder.  The descendant-renumber lineage
   event (uk_replay_descendant_renumber_provision) closes the
   _apply_same_provision_descendant_renumber gap; both renumber shapes now
   emit renumber-specific MutationEvents.
3. Continue caller-specific audit of same-label sorted insertion paths where a
   refusal should emit a more precise conflict diagnostic. The shared mutable
   helper and main body-root/renumber callers now prevent destructive
   replacement; remaining work is diagnostic quality for rarer refusal paths.
4. Promote the UK post-op invariant attribution shape into a small core
   invariant-delta carrier only after another frontend has a concrete consumer;
   for now UK owns the local projection because its invariant scope pruning is
   frontend-specific.
5. Centralize UK target-gap/absent-target diagnostic ladders only after a
   family-level shape is clear from real witnesses; do not collapse UK drafting
   semantics into core.

The highest-value order is now remaining UK mutation-event/debug gaps,
target-gap family centralization once real witnesses justify it, cautious
comparison-normalization reuse where a frontend already has named projection
rules, and small type-surface cleanup batches.

## Actuator 12 — UK table-selector regex hotspots (2026-05-29)

Site 1 (§1.11 applied): `_source_names_containing_target_for_table_cell` replaced
dynamic `re.search(rf"\\bin\\s+section\\s+{re.escape(label)}\\b", ...)` with (a)
`"in section" not in text.lower()` substring guard eliminating ~99% of calls
before any regex walk, and (b) `@functools.lru_cache(maxsize=2048)` factory
`_section_label_pattern(label)` keyed on label string — low cardinality (one per
target section), keeps patterns alive without unbounded retention.  22,696 calls /
~5,062 distinct (text, label) pairs on ukpga/1970/9 (4.5x repeat ratio, Sensor I
cand 2).

Site 2 (§1.11 applied): `_uk_source_parent_table_column_entry_omission_text_patch_claim`
inner re.search replaced with (a) `"entries relating to" not in lead_text.lower()`
substring guard before any regex, and (b) split into two module-scope patterns
`_UK_COLUMN_OMIT_ENTRIES_RELATING_RE` + `_UK_OMIT_FROM_COLUMN_ENTRIES_RELATING_RE`
— kills the alternation cross-product that caused 6.8 ms/call catastrophic
backtracking on non-matching inputs (same shape as Actuator 8).  Bounded `{0,300}?`
replaces `.*?` (Sensor I cand 3).  Combined wall-time saving: 30.49 s → 28.67 s
(−1.8 s) on ukpga/1970/9.  Adjudications identical (score=40.8%, replay=80.0%,
ops=2001, effect_rows=3265).  Note: cProfile cumtime overestimated the saving
because it is inclusive of shared callees; actual exclusive saving after Actuator 8
removed the dominant bottleneck is ~1.8 s combined.
- Sensor H batch 1 (Actuator 14, 2026-05-29): 7 HIGH-danger landmines closed in
  UK source_adjudication / effect_lowering_tail / table_sources /
  source_text_reclassifications cluster.  Sites: #1 (carried_tail .+/.+),
  #2 (repeal_schedule_table .+), #3 (period_specified inline .+), #4
  (scoped_occurrence three .*), #5 (amendment_table two .*), #6
  (column_substitution three lazy .*?), #7 (shall_apply .*).  Fix shape per
  §1.11: module-scope compile, .{0,N}? bounded quantifiers, substring fast-guards.
  32 new adversarial perf tests in tests/test_uk_regex_batch1_perf.py; all CI
  shards green (2367 passed).  Adjudications identical on ukpga/1970/9 bench
  (score=40.8%, replay=80.0%, ops=2001, effect_rows=3265, 28.85 s vs 29.44 s).
  Wall-time delta on ukpga/1970/9 is small (−0.6 s) because these patterns do
  not trigger heavily on that statute; expected savings accrue on statutes that
  exercise the scoped-occurrence / schedule-table / column-substitution paths.
- UK letter-suffix new-leaf insert promotion (`uk_effect_after_anchor_insert_promoted`,
  rule family `targeted_after_anchor_insert`) now promotes Replace→Insert at lowering
  time when all four conditions hold: (1) source payload actual_el is non-None (real
  XML, not synthesized by `infer_source_payload_from_target`), (2) payload kind+label
  matches the target leaf exactly, (3) instruction text contains "after [X] insert"
  pattern (gated by `_source_after_insertion_anchor`), and (4) the leaf has an
  alphanumeric-suffix label (`\d+[A-Za-z]+`, e.g. 3A, 1B, 6ZA). The anchor eid is
  derived from the letter-suffix numeric stem. Phase ownership: AGENTS.md §6 —
  replay-time `uk_replay_replace_materialized_as_insert_for_missing_leaf` remains
  as fallback for cases that don't carry all four source signals.  Anchor propagation:
  `UKSubstitutedPayloadInsertNormalization.anchor_preceding_eid` threads through
  `chained_insert_anchor_override` in `_lower_effect_target` to the finalization
  `chained_insert_preceding_eid` parameter.  Observations are nonblocking
  (strict_disposition=apply, quirks_disposition=apply) and registered in
  `UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS`.  Covered by
  `tests/test_uk_effect_after_anchor_insert_promotion.py` (26 tests: helper unit
  tests, positive promotion, observation shape/disposition, 5 negative guards
  including for-substitute instruction text, inferred payload, insert action,
  mismatched label, plain numeric target).
- UK block-substitution group tail promotion (`uk_effect_block_substitution_tail_promoted_to_insert_after`,
  rule family `targeted_after_anchor_insert`) implements Sensor K Pattern B: effects
  whose affected_provisions cover a range like `s. 25(4)-(4B)` decompose into a
  multi-target group.  Op `_0` (numeric stem) stays Replace; ops `_1..._n` (letter-suffix
  variants `4a`, `4b`) are promoted to InsertAfter at lowering time.  Anchor = immediately
  preceding target in the group (chain: `4a` anchors at `4`, `4b` anchors at `4a`).
  Detection signal: (a) target_index > 0 in a multi-target group, (b) current leaf
  label is a letter-suffix (e.g. `4a`), (c) group[0] leaf label equals the numeric
  stem, (d) payload matches target leaf, (e) source_payload_actual_el is non-None.
  Relationship to A13: Pattern B fires BEFORE the A13 instruction-text check.  A13
  remains as the tighter clean case (single-op "after X insert" pattern from instruction
  text).  Pattern B handles multi-op groups where instruction text is "for subsection (N)
  substitute—" (not "after X insert"), so A13's guard 4 would never match.
  Adjudication delta on smoke corpus: `uk_replay_replace_materialized_as_insert_for_missing_leaf`
  66 → 48 (−18); `uk_effect_block_substitution_tail_promoted_to_insert_after` 0 → 15.
  No new PROVED_REPLAY_BUG (replay_bug=29 before and after).  Witness statute
  ukpga/1978/29 score=24.0% replay=52.6% (stable vs 52.9% before; 3 ops shifted
  from Replace to InsertAfter as expected).  Covered by
  `tests/test_uk_effect_block_substitution_promotion.py` (26 tests: unit helpers
  for _block_substitution_tail_insert_detail, positive three-op group, observation
  shape/dispositions/blocking, anchor-EID chaining, 6 negative guards including
  payload mismatch, standalone tail, consecutive-numeric, no actual_el,
  stem mismatch).  Pattern A (partial repeal on missing leaf) remains at
  recovery per diagnosis; Pattern C (single letter-suffix substitution) is a
  separate follow-on actuator.
- `src/lawvm/core/regex_safety.py::lawvm_regex_risks` is the shared AST-based
  catastrophic-backtracking lint for module-scope ``_*_RE`` / ``_*_PATTERN``
  constants; validated by ``tests/test_regex_perf_gate.py`` (Sensor H batch 5).
  A18 (2026-05-29) enhanced ``first_chars()`` in both detectors to resolve
  CATEGORY escapes (``\d``, ``\w``, ``\s`` and Unicode variants) to concrete
  ASCII frozensets, eliminating 77 CATEGORY false-positive entries across 21
  files (219 → 142 total; gate allowlist reduced from 69 → 48 files). Genuine
  ``\w+\d+`` overlaps and ``.{0,N}?`` adjacent-repeat pairs still flagged
  correctly. Patterns like ``\d+[a-z]?`` now correctly reported as disjoint.
- Sensor H batch 6 (Actuator 19, 2026-05-29): 12+8 HIGH-danger landmines closed
  across `sweden/grafter.py` (12 violations) and `uk_legislation/source_parent_payloads.py`
  (8 violations; 4 files total including `source_fragment_context.py`,
  `source_text_reclassifications.py`).  Fix shapes: (a) `\s+` inside optional groups
  → bounded `\s{1,N}`; (b) `(?:X\s*)?` nested-quantifier optionals → BRANCH `(X|)`;
  (c) `[^,]+` unbounded char-class repeats → `[^,]{1,N}`; (d) `\s+` in lookahead
  `(?!\bdels\s+att\b)` → literal `(?!dels att)` eliminating nested quantifier in
  zero-width assertion; (e) space before em/en/hyphen-dash handled via ` ?[—–-]`
  (was missing, caused 2 test failures in `test_uk_schedule_compile.py`); (f) `of
  section [0-9A-Za-z]{1,20}` context → `of [^,]{1,120}` to cover long act-reference
  contexts like `of section 86 of the 1990 Act (period of licences)`.  All 4 files
  removed from `_KNOWN_UNFIXED` allowlist.  32 adversarial perf tests added in
  `tests/test_regex_batch6_perf.py` (all pass).  Full suite: 10916 passed, 0 failed.
  Bench ukpga/1970/9: score=40.8% (unchanged), wall=1.60 s.  Byte-level edits used
  for files containing curly-quote chars to avoid Edit-tool string-delimiter corruption.
- §1.9 Cluster B + C cleanup (2026-05-29): `UKMutableNode` and `LegalOperation.payload` /
  `provenance_tags` typed-carrier getattr drift removed. Cluster B: 5 sites in
  `replay_repeal_apply.py` (children/kind/label/text on `UKMutableNode`) and 9 sites in
  `replay_target_gaps.py` (kind/children on `UKMutableNode` in `uk_broad_schedule_table_shape_gap`).
  Cluster C: 7 `getattr(op, "payload", None)` → `op.payload` in `replay_target_gaps.py`
  (functions `uk_payload_shape_invariant_violation_records`, `uk_payload_container_shape_gap`,
  `uk_repeated_form_label_payload_shape_gap`, `uk_existing_target_insert_gap`,
  `uk_existing_target_insert_already_materialized`, `uk_existing_target_insert_conflict_detail`,
  `uk_crossheading_insert_target_gap`); downstream payload `kind`/`label` getattr collapsed to
  `payload.kind.value` / `payload.label`. `text_rewrite_fragments.py` cluster C
  `provenance_tags` sites were already clean (A20 covered them). Pattern: A6 handled
  `LegalAddress.path` / `LegalOperation.target` (22 sites); A20 handled `_witness_for_op`
  return-type root cause and cascaded 17 sites. This commit closes the remaining Sensor E
  clusters. Shard ownership fix also included: `test_regex_batch6_perf.py` added to `uk`
  shard in `scripts/test_shard.py` (was unassigned, blocking CI). Adjudication counts
  identical (4532). ty green. 2431 uk-shard tests passed.
- Sensor H batch 4 / AGENTS.md §1.11 module-scope regex hygiene (Actuator 23, 2026-05-29):
  3 commits across 3 files.
  (1) `uk_legislation/source_parent_payloads.py`: 1 site — parametrized alpha-label continuation
  pattern in `_source_carried_top_level_alpha_matches` wrapped with `@lru_cache(maxsize=256)`
  factory `_uk_alpha_continuation_re(expected)`. `functools.lru_cache` import added.
  (2) `finland/scope.py`: 3 sites in `restrict_sec1_fallback_to_parent` — `_FI_NUMBERED_ITEM_RE`
  and `_FI_CUT_RE` lifted as static constants; bracketed-citation pattern lifted as
  `@lru_cache(maxsize=1024)` factory `_fi_statute_citation_re(parent_id)` (returns `None` when
  parent_id parse fails; replaces the original try/except early-return). `functools.lru_cache`
  import added.
  (3) `estonia/grafter.py`: 18 sites total. Static lifts: `_EE_ITEM_LABEL_RE`,
  `_EE_QUOTED_TITLE_RE` (2 duplicate sites), `_EE_ALPHA_WORD_RE`,
  `_EE_MINISTRY_WORD_REPLACE_EXCEPTION_RE`, `_EE_MINISTRY_SECTION_REF_RE`.
  New lru_cache factories: `_ee_cross_act_repeal_re(target_title)` (maxsize=512),
  `_ee_text_replace_regex_ci(old_variant)` (maxsize=8192; used at 6 call sites),
  `_ee_surface_pattern_compiled_ci(surface)` (maxsize=4096),
  `_ee_lahter_heading_re(lahter_label)` (maxsize=512),
  `_ee_inline_item_label_re(item_label)` (maxsize=512).
  Existing `_ee_text_replace_regex` cache used at 1 additional site
  (`_ee_text_replace_match_spans`).
  Skipped: 3 patterns in `_ee_global_generic_minister_plural_replace` (`singular_pattern`,
  `shared_head_pattern`, `redundant_tail_pattern`) — parametrized on `old_titles`
  (unhashable list) and called once per statute cold pass; not worthwhile to restructure.
  All byte-identical to original patterns (verified via raw bytes for curly-quote chars).
  Bench parity: UK score=40.8% (ukpga/1970/9, unchanged); FI structural accuracy=95.06%,
  mean error=4.94%, 346 perfect (matches prior run ee_wide_after_textual_invalidation).
  EE Total 2200 OK (unchanged). ty + all CI shards green. Regex perf gate (core_ir_contracts
  shard) passes for new module-scope constants.

- UK `parse_fragment_substitution` short-circuits on inputs missing any operative
  verb ("substitut", "insert", "omit", "replac", "become", "repeal", "cease")
  and the multi-occurrence pattern `_UK_MULTI_OCCURRENCE_SUBSTITUTION_RE` is lifted to
  module scope with bounded char classes (`[^"'\u201c\u201d\u2018\u2019]{0,500}?`) and
  a bounded `{0,5}` list repeat. A stack snapshot of a hung tight-bench worker showed
  this function as the sole CPU consumer for 30+ minutes; adversarial input now finishes
  in <100 ms via the substring guard and the bounded regex.

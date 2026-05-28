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
- branch lifecycle events now record proposal/draft status history as graph
  facts, separate from enacted-state operation replay.
- `CorpusGraph` now rejects duplicate branch ids and dangling branch edge or
  lifecycle references, keeping proposal/draft graph claims internally
  referentially sound.
- branch graph edges and branch-impact projection rows now carry `scenario_id`,
  and `CorpusGraph` rejects branch-edge scenario mismatches against the
  registered branch so proposal variants cannot merge by branch id alone;
  branch-impact row ids include scenario ids when present.
- branch overlay operation selection now has a core helper for explicit
  current-law-plus-selected-branch materialization demos without promoting
  proposal claims into the default enacted lane.
- Neo4j graph export now includes branch nodes, branch would-affect edges, and
  branch lifecycle event tables so proposal/draft graph facts have a persistent
  export lane.
- JSON-LD graph export now includes branch resources, branch graph edges, and
  branch lifecycle events under the `lawvm:` namespace.
- `lawvm branch-demo` now emits a synthetic branch/authority payload for
  demonstrating default enacted vs selected proposal operation lanes.
- target-resolution evidence now has a neutral core carrier for source target,
  candidate count, selected target, status, confidence, and strict/quirks
  disposition; frontends still own candidate discovery and local fallback
  policy.
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

## Keep Local For Now

Do not promote yet:

- Finnish clause grammar and sparse payload binding rules;
- Estonian morphology-specific replacement generation;
- UK effect-feed target-shape parsing and manual frontier classifiers;
- Open Law XML/codify namespace parsing;
- Sweden/Norway acquisition-specific source lane decisions.

These are jurisdiction source semantics, not shared invariants.

## Recommended Next Work

1. Continue replacing local path tuple spellings with shared aliases such as
   `TreePath` / `TreePaths` where that reduces type noise without changing
   serialized evidence. The shared aliases now live in core; remaining work is
   opportunistic call-site cleanup rather than semantic promotion.
2. Extend UK mutation-event emission to any remaining direct structural
   mutation helpers not routed through central replace/remove/insert or the
   table/schedule children-splice recorder.
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

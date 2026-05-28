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
- UK replay now has an opt-in core `MutationEvent` sink for central node
  replacements, removals, and ordinary insertions via `_replace_node_in_statute`,
  `_remove_node`, `_record_child_inserted`, and `_record_supplement_inserted`;
  whole-act repeal is also recorded as a root-path removal.
- UK direct table row, table column, schedule-table row, and schedule-list
  entry splices now emit conservative container-scoped mutation events. Table
  row events record the table path instead of inventing row-level identity for
  unlabeled duplicate table rows.
- UK schedule-list entry insert/repeal/replace helpers now route child-list
  replacement plus mutation-event recording through one local wrapper; grouped
  schedule-entry insertion is covered by a mutation-event regression.
- UK same-parent sibling renumber replay now emits a renumber-specific mutation
  event with old and new paths instead of reporting the destination as an
  ordinary insertion.
- mutation path type aliases (`TreePaths`, `RenumberedTreePaths`) now live in
  the core mutation-boundary surface instead of Finland-local apply helpers.

## Ranked Promotion Candidates

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
- Estonia RT acquisition diagnostic record projections now delegate their
  shared envelope fields to `diagnostic_detail`.
- Sweden replay adjudication helpers now delegate the same replay envelope
  defaults to `diagnostic_detail`.
- Sweden acquisition diagnostics for official artifacts and RK current JSON now
  delegate source-pathology envelope defaults to `diagnostic_detail`.
- EU pipeline diagnostics and replay adjudication helpers now delegate their
  shared envelope defaults to `diagnostic_detail`.
- EU parser extraction diagnostics now delegate their shared envelope defaults
  to `diagnostic_detail`.
- EU Cellar acquisition diagnostics and REUL bridge target-resolution
  diagnostics now delegate their shared envelope fields to `diagnostic_detail`.
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

The highest-value order is now cautious comparison-normalization reuse for
additional frontends, remaining UK mutation-event/debug gaps, and small
type-surface cleanup batches.

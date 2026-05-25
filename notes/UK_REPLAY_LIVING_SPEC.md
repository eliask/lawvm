# UK Replay Living Spec

Status: living notes for replay/adjudication behavior, intentionally incomplete.

Purpose:

- record UK-specific replay invariants that are stable enough to preserve
- separate real replay semantics from compare-only and source-only residue
- keep the current UK frontier legible while broader compiler/front-end rebuilds wait

Related:

- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md)

## 1. Replay Boundary

For UK, replay should execute already-typed operations.
It should not recover amendment meaning by broad textual fallback once ops exist.

Current practical rule:

- parser/compiler fixes are preferred when a row compiles to the wrong target family
- executor fixes are preferred when a correct op mutates the wrong structural slot
- compare/source classes are preferred when the replayed state is coherent but the oracle or extracted source is not

UK source-only replay and UK manual-claim replay are separate regimes.

The default UK replay intent is still source-text-only: compile executable
operations from archived public source text, effect metadata, and deterministic
frontend rules. A manual or LLM-assisted claim may be useful for hard rows, but
it must enter through an explicit claim ledger and validator. It must not be
folded into parser fallbacks or executor heuristics just to raise replay score.

If a benchmark uses manual claims, the report must say so. Source-only scores
remain the measure of what LawVM can prove from public machine-readable
surfaces without additional editorial compilation.

## 2. Grounding Invariants

`ground_ids()` is allowed to align replayed nodes to oracle EIDs.
It is not allowed to invent cross-branch meaning.

Current UK-specific invariants:

- replay entry points must agree on pre-application filtering
  - `replay_uk_ops()` and `UKReplayPipeline.apply_ops()` must share the same
    whole-act skip and oracle-zombie-collapse behavior
  - a source-bad whole-act repeal row must not zero out a statute in one entry
    point while being skipped in another
- retained repeal branches can preserve their subtree when oracle proves the
  descendants still exist
  - if a repeal target root is retained in oracle and oracle still exposes
    descendant EIDs under that root, replay should preserve the subtree instead
    of collapsing to the root only
  - this keeps later text-level amendments on those descendants replayable
- schedule-local structural wrappers must not steal bare oracle EIDs from other schedules
  - a `crossheading` or `part` inside `schedule-3` must not ground via a global bare key like `crossheading-foo`
- schedule-local `part` fallback IDs keep their kind prefix
  - `Sch. 3 Part II` falls back to `schedule-3-part-2`, not `schedule-3-2`
- schedule-local `part`/`chapter` grounding strips a duplicated source kind
  prefix from the local label before hierarchical oracle lookup
  - a source label like `PART 9A` under `schedule-2` must ground through
    `schedule-2:part-9a` to `schedule-2-part-9a`, not to
    `schedule-2-part-part-9a` or a space-bearing fallback
- deep roman descendant labels preserve roman suffixes in fallback IDs
  - `section-88-3c-b-ii` and `schedule-7a-paragraph-9-2-b-ii` are correct local fallback shapes
  - replay must not emit numeric fake tails like `...-1` / `...-2` where oracle uses `...-i` / `...-ii`
- oracle/local fallback IDs require a stable visible label or a structural
  container role
  - replay must not synthesize public fallback IDs such as `section-7-item`
    for unlabeled non-container nodes, because repeated unlabeled siblings
    would collide and create replay-only benchmark identities
  - unlabeled non-container source shape remains addressable only through the
    surrounding target/operation evidence unless a later source phase assigns
    a stable legal label
- structural replacement preserves existing target identity from either source
  `eId` or source `id`
  - replacing a found node may not drop its identity because the base parser
    carried the identifier as `id` instead of `eId`
  - current witness: `asc/2021/1` / `wsi/2021/1349 reg. 42(3)(b)`, replacing
    `Sch. 5 para. 9(c)`
- exact replay target identity wins before fuzzy schedule traversal
  - if a target address derives a concrete EID and that EID exists in the live
    tree, replay must bind that node before trying schedule ordinal `p1group`
    recovery
  - the concrete-EID fast path must still match the target leaf kind and label.
    If the derived EID is coarser than the legal address, replay must ignore it
    and resolve the explicit path instead of letting a child repeal/delete its
    parent.
  - ordinal wrapper recovery is only for absent exact identity; it may not steal
    an explicit schedule paragraph/subparagraph/item target from another branch
  - current witnesses: `asp/2001/10` / `asp/2014/14`, Schedule 3 paragraph
    2(2) and related text replacements; `ukpga/2020/17` s. 265(1)(b)(i)
    repealed by `ukpga/2021/11 s. 22(3)(a)(i)`, where the coarse derived EID
    `section-265-1-b` must not delete paragraph `(b)` when the source targeted
    subparagraph `(i)`.
- strict top-scoped EID searches may not escape their root after a miss
  - if an EID such as `section-1-p1` has a live top root `section-1`, replay
    may search inside that root with sequence matching, but a miss must remain
    a miss
  - replay must not then scan the whole body and accept a suffix/sequence match
    like `chapter-3-section-1-p1`; that would be target hijacking across an
    explicit source identity boundary
  - if the top root itself is absent, separate source-pathology or recovery
    evidence is required before rebinding to another branch
  - this boundary applies to both exact and sequence-match lookup lanes; a
    sequence alias cannot justify crossing an absent or failed strict top root
- broad schedule/part text patches require a replay-visible text-bearing shape
  - if a broad schedule or schedule part target has no table nodes and no
    provision descendants carrying the preimage, replay should classify the row
    as `uk_replay_broad_schedule_table_shape_gap` rather than a generic
    text-match miss or explicit table-target gap
  - schedule parts use the narrower
    `uk_replay_broad_schedule_part_table_shape_gap`, because the missing
    carrier is a table/provision shape under a known part rather than the whole
    schedule root
  - current witness family: Appropriation Act schedule amendments such as
    `asp/2002/7` / `ssi/2003/157`
- broad schedule/part structural replacements require source-owned descendant
  coverage
  - if the effect target is a whole schedule or schedule part but the extracted
    payload is only flat `BlockAmendment` text, lowering must reject the row as
    `uk_effect_broad_schedule_flat_payload_rejected` instead of replacing the
    target root
  - this is payload-smuggling prevention: a naked table row or amount entry does
    not authorize deletion of unclaimed parts, tables, rows, or entries under
    the schedule root
  - strict mode blocks; quirks mode records and leaves the live target intact
  - current witness: `asp/2000/2` / `ssi/2000/307 art. 2`, where a row-like
    `BlockAmendment` payload was previously replayed as `replace schedule:2`
- structural parsing preserves local lead text even when the provision has
  child provisions
  - a subsection with `(a)` / `(b)` children may still have operative lead text
    before those children
  - replay text patches must be able to target that lead text without flattening
    or absorbing child provisions
  - current witness: `asc/2021/1` / `asc/2024/5 Sch. 1 para. 15`, replacing
    lead text in `s. 142(1)` and related provisions
- a source-backed structural replacement payload that already matches the
  explicit target leaf stays a structural replacement even if its own text
  contains nested `for ... substitute ...` instructions
  - nested amendment language inside the replacement payload is legal text to
    preserve, not permission to reclassify the parent action as a text patch
  - current witness: `asc/2023/3` / `wsi/2024/1061 reg. 8`, replacing
    `Sch. 13 para. 117`
- whole-schedule inserted payloads may synthesize descendant EIDs only at payload normalization time
  - rule: `uk_whole_schedule_payload_descendant_eid_synthesis`
  - this is allowed only when the effect target is an explicit single schedule address and the root schedule EID is derived from that target
  - descendant IDs are derived from the target-owned root plus parsed source labels; oracle text/hash/fuzzy matching must not participate
  - source-visible crossheading text in the inserted schedule payload may derive
    `crossheading-<heading-slug>` identities under that target-owned root
  - synthesized crossheading IDs do not become the parent identity for numbered
    descendants; numbered paragraphs/subparagraphs/items remain rooted in the
    explicit schedule target
  - source-provided descendant EIDs are preserved
  - if repeated local labels would create the same synthetic descendant EID
    under the same schedule payload, synthesis must skip the repeated identity
    and record `skipped_duplicate_count`; duplicate identity is not repaired by
    suffixing or guessing a new legal address
  - strict lowering can disable the synthesis and emits a blocking record rather than silently inventing descendant identity
  - current witness: `asc/2021/1` / `wsi/2022/797 reg. 5`, inserting `Sch. 10A`
- schedule paragraph flattening is narrow
  - flattening to bare suffixes is only for the established paragraph/item family
  - it must not strip `part-` or other structural prefixes
- substituted-series rows that name the replaced old series and a single new alphanumeric provision
  - when metadata says `substituted for s. 3(5)(6)` and the affected provision is `s. 3(5A)`,
    replay must not target a synthetic missing `5A` node directly
  - compile as:
    - `replace` the first replaced anchor (`5`) with payload label `5A`
    - `repeal` the remaining replaced anchors (`6`, etc.)
  - otherwise replay can silently retain the old tail and mutate the wrong anchor slot
- substituted-series rows may also name the first replaced anchor as the affected provision
  - when metadata/source says `substituted for s. 5(5)(6)` and the affected provision is
    `s. 5(5)`, the replacement remains bounded to subsection `5`
  - compile the same explicit trailing-anchor repeal for `s. 5(6)`; do not widen the
    replacement operation to the whole `s. 5(5)-(6)` range
  - current witness: `asp/2002/5` / `ukpga/2014/23 Sch. 1 para. 3(4)(c)`
- commencement rows are non-mutating for replay-state purposes
  - `coming into force` / `commencement order` list items must compile to no IR ops
  - they matter for commencement gating, not for structural text mutation
  - broad fallback heuristics must not recover fake `replace` actions from citation/list text like
    `section 63 (deduction of trade union subscriptions from wages in public sector);`
- non-structural replay fallback is applied-only and narrow
  - `UKReplayPipeline._should_replay_nonstructural_ops()` may admit:
    - all-`replace` substituted-for sibling families
    - `replace + trailing repeal` substituted-series anchor families
    - `revoked` rows that compile to structural repeals
    - `added` rows only when the exact affecting source is extractable and
      compiles to source-owned structural inserts; this emits
      `uk_effect_added_type_source_structuralized` and does not permit
      metadata-only backfill
    - metadata pseudo-definition targets such as
      `Sch. 2 Pt. 1 para. 1(1) (defn. of "the 1996 Act")` are not ordinary
      structural paths. Whole-entry replacements and unanchored
      multi-definition rows must emit blocking
      `uk_effect_structural_pseudo_definition_target_rejected` until a
      definition-entry/list-entry compiler proves the carrier, payload
      semantics, and placement. Manual-frontier triage splits this family:
      rows whose extracted source carries a quoted definition-entry payload are
      `uk_manual_frontier_structural_pseudo_definition_entry_placement_candidate`;
      rows whose source is only a schedule/gateway/header fragment are
      `uk_manual_frontier_structural_pseudo_definition_source_insufficient`.
      A narrower child-substitution lane is owned:
      when metadata names exactly one definition term plus one child label
      and the resolved affecting source is a `BlockAmendment` or
      `InlineAmendment` containing only replacement child text, lowering
      strips the pseudo metadata tail and emits
      `uk_effect_metadata_pseudo_definition_child_substitution_text_patch`
      with selector `TEXT_DEFINITION_CHILD_PARAGRAPH_<term><US><label>`.
      A second narrow lane is owned for `SourceRange` pseudo-definition entry
      inserts: when each source child row says `after the definition of "X"
      there is inserted`, lowering emits one
      `uk_effect_source_range_definition_entry_insert_text_patch` per row
      with selector `TEXT_AFTER_DEFINITION_X`. If the same bounded source
      range also contains `at the end there is inserted` and the row payload is
      a definition-entry payload, lowering emits
      `uk_effect_source_range_definition_entry_list_end_schedule_entry_insert`
      as a typed `SCHEDULE_ENTRY` insert carrying a
      `schedule_list_entry_selector` with
      `placement_family=definition_list_end_from_source_range`. Replay may
      apply that selector only after resolving the pseudo-stripped carrier and
      proving it has direct `schedule_entry` children; success emits
      `uk_replay_schedule_list_entry_end_position_resolved`, while missing
      direct entries remains a blocking
      `uk_replay_schedule_list_entry_anchor_unresolved`. Current witnesses:
      supported child substitution
      `key-84dfb84c1b3bb0bfda9c49643d2f3363`; full source-range entry
      insertion with anchored rows plus list-end row
      `key-b6898062023bb5f7be14b433d0932f78`.
    - compound affecting-source references that combine a gateway provision
      with a schedule payload, such as
      `s. 73 Sch. 2 Pt. 1 para. 1(2)(a)`, must not stop at the gateway
      section if the explicit schedule component resolves. Source selection
      records `uk_affecting_act_compound_reference_split_fallback` with
      `split_selected_part=second` and passes the payload component to
      lowering. If that second component is a parenthesized range such as
      `Sch. 2 Pt. 1 para. 1(2)(d)-(f)`, source extraction emits
      `uk_affecting_act_parenthesized_range_source_extracted` over exactly
      the addressed child rows. If the current affecting-act XML contains
      only dot-leader shells for every row in that synthetic range, source
      lane selection must use enacted XML and record
      `uk_affecting_act_current_shell_enacted_source_selected`. This is still
      not permission to replay entry or multi-definition pseudo targets
      without the compiler above.
  - it must not admit unapplied rows just because they compile cleanly
  - otherwise replay can materialize future-shape families like `s. 35(2)-(12)` and pollute the live frontier

## 3. Typed Non-Replay Residue

The UK frontier is no longer dominated by executor semantics.
The main non-replay residue classes now include:

- source pathology
  - `missing_extracted_source`
  - `instruction_text_reused_as_payload`
  - `broad_source_reused_as_payload`
  - `fragment_context_missing`
  - `reference_only_source_fragment`
  - `nonstructural_root_gap`
  - `non_substantive_shell_payload`
- compare shape
  - `collapsed_subtree_oracle_shape`
  - `descendant_only_oracle_wrapper`
  - `legacy_labeled_oracle_shape`
  - `oracle_missing_live_branch`
  - `retained_repeal_oracle_branch`
  - `text_patch_preimage_absent_from_target_surfaces`
  - `territorial_extension_oracle_gap`

UK work should continue to add typed classes only when a deterministic archive-backed pattern repeats.

Manual frontier claim templates:

- `lawvm uk-candidates --manual-compile-evidence-jsonl` may attach
  `lawvm.uk_semantic_compile_claim_template.v1` rows for known manual or
  deterministic-frontier families
- these templates are non-executable review scaffolds; they do not authorize
  replay and must remain marked `template_only_not_validated` /
  `executable=false`
- a claim template should state the action family, placement family, source
  witness, required ownership, required validator checks, and mutation-boundary
  checks before any future manual/LLM/human compiled claim can become executable
- current template-covered UK hard families include heading/crossheading/note
  facets, appropriate-place structural inserts, table surface mutations,
  definition-child plus tail substitutions, source-carried child/tail text
  rewrites, source-carried structured payloads, range-to-container
  substitutions, amendment-program targets, and cross-container renumber /
  migration rows
- cross-container renumber rows are lineage work, not same-parent relabels:
  a future executable claim must own source identity, destination identity,
  descendant wrapping or relabel semantics, lineage/migration events, and both
  source/destination mutation boundaries
- source-carried structured-tail substitutions are not ordinary text patches:
  the source substitutes a tail range with visibly structured child material,
  so a future executable claim must materialize child payload units rather than
  flattening replacement text into the parent host

Replay adjudication ownership:

- every `uk_replay_*` adjudication emitted by the UK replay executor must belong
  to an explicit classifier bucket in `source_adjudication.py`
- direct executor failures that prove replay could not execute its typed
  operation belong in `UK_REPLAY_BUG_ADJUDICATION_KINDS` and may promote a
  replay-vs-oracle residual to `PROVED_REPLAY_BUG`
- source/live shape gaps belong in `UK_REPLAY_SOURCE_SHAPE_ADJUDICATION_KINDS`
  and remain `UNRESOLVED` unless a later source witness proves the oracle or
  replay side wrong
- text-selector surface problems belong in
  `UK_REPLAY_TEXT_SURFACE_ADJUDICATION_KINDS`; these explain why a text patch
  could not be applied, but do not by themselves prove a LawVM replay bug
- successful narrow recoveries and already-materialized no-op observations
  belong in `UK_REPLAY_NONBLOCKING_OBSERVATION_KINDS`; these are evidence
  records only and must not promote a residual to a replay bug
- shared replay lint observations such as `text_duplication_warning` are also
  nonblocking UK replay observations when emitted by the UK executor. They may
  reveal suspicious replay text, but their own detail already records
  `blocking=false` / `strict_disposition=record`, so they must not sit in the
  unknown bucket or promote a residual by themselves.
- new replay adjudication kinds require both a classifier bucket and a
  regression test so unsupported source lanes cannot disappear silently
- replay adjudication bucket counts must be visible in the operator surfaces
  used for triage: `uk-replay`, `uk-bench`, `uk-candidates`, and UK evidence
  bundles/reviews. A no-replay/source-unavailable bundle should still carry an
  explicit zero-count adjudication summary so the lane is not absent by
  accident.
- UK evidence bundles should also expose the residual claim decision that those
  adjudications drive: selected tier/kind, residual side counts, comparison
  class, and whether a replay-bug section claim was emitted. The adjudications
  explain the inputs; the residual summary explains the proof promotion or
  non-promotion.
- Saved UK bench rows and `uk-candidates` rows/summaries must carry the same
  residual claim decision. Candidate triage that starts from a saved bench run
  must not infer "clean" from missing residual fields; legacy rows without this
  lane are `UNRESOLVED/unknown_legacy_missing`.
- UK benchmark history should aggregate residual claim tiers/kinds and emitted
  section-claim counts alongside replay adjudication totals, so long-running
  benchmark trends separate "replay adjudication observed" from "proved replay
  bug claim emitted".
- source-unavailable UK evidence bundles must expose an explicit
  `not_run_source_unavailable` residual claim summary rather than omitting the
  residual lane. Absence of enacted/oracle XML is acquisition evidence, not a
  clean replay residual.

Concrete compare invariant:

- pure text edits on a live subsection can still be compare-only
  - if base and oracle both expose the target root EID
  - and replay/base have no descendant EIDs there
  - but oracle exposes descendant EIDs under the same root
  - then the row is compare-only collapsed subtree shape, not a replay failure
  - current example: `ukpga/2002/21` `s. 28(1)`

Current applied-row invariant:

- `ukpga/2025/36` style commencement rows are not replay candidates once compilation is correct
  - after the explicit commencement no-op rule, the live `s. 63` row types `nonstructural_root_gap`
    with `Compiled ops: 0` and `candidate=no`

### Manual Compilation Frontier

Manual compilation is appropriate for UK only after deterministic extraction has
made the blocker phase-local and visible. It should not be used as a generic
escape hatch for parser gaps.

#### Rows that should stay deterministic

These should be handled by ordinary parser/lowering/executor work, not manual
claims:

  - source text contains explicit old text, new text, action verb, and target
    surface
- target facet is already represented by a canonical operation family
- payload is structurally owned by the affected target
- extent and effective-date metadata are already available or explicitly
  nonblocking

Examples:

- `for "X" substitute "Y"`
- `after paragraph (a) insert "Y"`
- `for the opening words substitute "Y"`
- `from "X" to the end substitute "Y"` when the span is uniquely bounded

#### Rows that are good manual-compile candidates

These can become manual work items because the public source likely contains
enough evidence for a human or LLM to propose closed operations, but the current
frontend cannot yet lower them safely:

- heading/title/sidenote targets that are not explicit word substitutions,
  omissions, `at the end insert ...` appends, or `after "X" insert "Y"`
  insertions against a quoted heading anchor. Explicit word-level heading
  patches can lower to `section:.../heading`; append lowers as a typed
  `TextPatchKindEnum.APPEND` patch; quoted-anchor heading insertion lowers via
  `uk_effect_heading_facet_after_anchor_insert_text_patch`. Other heading
  inserts still need a typed placement/compiler lane rather than a whole-body
  or synthetic text replacement. Schedule heading/title/sidenote refs are
  heading facets too; they must not be lowered through schedule-list-entry
  insertion just because their structural carrier is `Sch. N`.
- cross-heading replacements lower only when the source gives an explicit
  `heading before paragraph/section/article X substitute ...` whole-heading
  shape, or a quoted text patch against the cross-heading before a named
  paragraph/section/article. The owned lanes are
  `uk_effect_crossheading_before_anchor_replacement_lowered` for whole-heading
  replacement and `uk_effect_crossheading_before_anchor_text_patch_lowered` for
  quoted text patches; both target `X/heading`, use named text rewrite rules,
  and replay may mutate the crossheading parent only when `X` is the first
  structural child under that parent. A compound `paragraph X and cross-heading`
  replacement may also lower through
  `uk_effect_crossheading_and_structural_replacement_split_lowered` when the
  affecting source payload is a titled wrapper whose first structural child is
  exactly `X`; lowering emits a separate `X/heading` patch plus the ordinary
  structural replacement for `X`. A compound `paragraph/section/article X and
  cross-heading` repeal may lower through
  `uk_effect_crossheading_and_structural_repeal_lowered` only when the source
  text explicitly says the named structural target and the heading above it are
  repealed or omitted. Replay then removes the heading wrapper only if it has
  heading text and exactly one structural child, the claimed target; unresolved
  shared wrappers emit `uk_replay_crossheading_and_structural_repeal_unresolved`
  instead of deleting siblings. Other cross-heading replacements remain blocked
  by `uk_effect_crossheading_replace_rejected`.
- repeal schedules and table rows where the table columns identify enactment
  and extent of repeal
- definition insertions where the source context supplies the anchor and the
  extracted payload alone is insufficient. A carried definition-entry payload
  may lower via `uk_effect_source_carried_definition_entry_insert_text_patch`
  when the parent source instruction explicitly says `after the definition of
  "X" insert...` and the extracted payload is itself a definition entry;
  otherwise the row remains blocked rather than guessed into the target section.
  The parent witness must be source-local to the payload row: broad containers
  such as a `Pblock`/`P1` that also contain earlier sibling amendment rows
  cannot lend a prior `for the definition of "Y" substitute...` context to a
  later `after the definition of "X" insert...` payload. Current witness:
  `asp/2000/4` effect `key-78605c6a5376f3a9f6955c985964d597` from
  `ssi/2005/465 Sch. 1 para. 28(9)`.
- `at the appropriate place` insertions, until a placement compiler can prove
  the target anchor without guessing from live text. A child source instruction
  of the form `at the appropriate place insert— <definition entry>` does not
  inherit an anchor merely because a broad schedule/paragraph ancestor contains
  another `after/for the definition of "X" ...` formula. Lowering must reject
  the row under `uk_effect_appropriate_place_definition_entry_insert_rejected`
  with reason
  `appropriate_place_definition_entry_requires_anchor_claim`, leaving it for a
  manual/auditable placement claim. Current witness: `asp/2001/2` affected
  `s. 48(1)` by `asp/2019/17 Sch. para. 3(6)(a)(iii)/(vi)/(viii)`, with
  analogous `s. 82(1)` rows in the same schedule paragraph.
- grouped title/heading substitutions that do not name exact old and new text
  for each executable patch. Explicit ranges such as `In the titles to sections
  10 to 14 "A" and "B", wherever these expressions occur, become,
  respectively, "C" and "D"` lower under
  `uk_effect_heading_facet_range_expanded` plus
  `uk_effect_respectively_all_occurrences_substitution_text_patch`; each
  section heading receives one all-occurrences text patch per quoted pair.

Deterministic substituted-series lowering:

- source rows shaped as `substituted for <old sibling>` with affected metadata
  expanding to `<old sibling>, <new sibling...>` may lower later source-owned
  sibling payloads as inserts under
  `uk_effect_substituted_series_new_sibling_insert_lowered`
- source rows shaped as `substituted for <old sibling>` with affected metadata
  expanding to `<new sibling...>, <old sibling>` may lower earlier
  source-owned sibling payloads as inserts under
  `uk_effect_substituted_series_pre_anchor_sibling_insert_lowered`; the
  source-named old sibling remains the replace anchor. Current witness:
  `ukpga/2020/17` affected `Sch. 21 para. 5A 6` by `ukpga/2022/32 s. 127`,
  where paragraph `5A` is a new source-owned sibling before the replaced
  paragraph `6`.
- source rows shaped as `substituted for <old sibling>` with affected metadata
  naming the new sibling label, or one-for-one old/new sibling series, may
  rebind the executable replace target to the source-named old sibling under
  `uk_effect_substituted_for_label_changing_target_rebound`; replay records
  `uk_replay_source_label_changing_substitution_resolved` and preserves each new
  payload label/eId rather than silently carrying old sibling identity
- word-level rows where source text explicitly says `In paragraph X of schedule
  Y to ...` but effect metadata names another paragraph in the same schedule may
  lower to the source-named paragraph under
  `uk_effect_source_text_schedule_paragraph_target_overrides_metadata`; this is
  a source-vs-metadata conflict record, not a live-tree search
- source rows shaped as `for paragraphs (a) and (b) substitute` with one
  compressed metadata range such as `Sch. 4 para. 11(a)-(ba)` may expand the
  target range from the direct `BlockAmendment` payload children under
  `uk_effect_source_payload_sibling_range_expanded`; when the source names only
  the first two siblings as replaced but the payload contains an extra labelled
  sibling, the extra payload lowers as an insert under
  `uk_effect_substituted_range_extra_payload_sibling_insert_lowered`
- this is not a generic action-family rewrite: the old target must be the first
  expanded target, every inserted sibling must share the same parent and leaf
  kind, and each inserted payload must carry the same label as its target
- label-family checks must preserve alphabetic item labels (`d`, `e`, etc.)
  rather than Roman-normalizing them
- if the source payload does not uniquely identify the later sibling, the row
  remains a blocked or manual frontier case rather than relabelling one payload
  onto another target

The work item should carry:

- effect row metadata
- affecting source XML fragment and nearby context
- affected base target subtree
- oracle target subtree where available
- current lowering rejection and source-pathology records
- candidate target addresses/facets

`uk-effects --evidence-jsonl PATH` exports the selected effect rows as
`lawvm.uk_manual_compile_frontier.v1` JSONL work items. This is an evidence
surface, not a replay shortcut: each row must keep the stable workqueue
`rule_id`, manual frontier status/rule/reason, source witness URLs/status,
bounded source preview, source-pathology class, lowering rejection counts, and
`strict_disposition=record`. Each row gets a deterministic `work_item_id`
derived from the statute/effect/source-preview/manual-frontier identity so
manual work can be deduplicated outside the original report. Rows explicitly
declare `claim_kind=semantic_compile`,
`claim_status=unresolved_work_item`, `validator_status=not_validated`, and a
SHA-256 hash of the bounded source preview. Rows also carry an
`affecting_source_witness` block with the affecting act/provision, source
status, byte size, and SHA-256 hash when the affecting XML is available. The
base/oracle `source_witness` block also preserves source SHA-256 hashes when
the archived source bytes are present.
The export must be paired with `--manual-compile-status` or
`--manual-compile-rule`; otherwise deterministic or source-insufficient rows
could be silently packaged as manual work.
Rows also preserve full lowering/source-acquisition rejection records and a
bounded target context (`affected_provisions`, resolver EIDs if known, and the
compare shape) so a copied work item remains auditable without reopening the
full effect report.
Known manual families may additionally include a non-executable
`suggested_claim_template` with `claim_status=template_only_not_validated`.
Rows also carry `suggested_claim_template_status`, either `available` or
`not_available`, so missing templates are visible rather than silently
represented by an empty object. The same non-executable template surface is
also emitted by single-effect `uk-effect --json` reports so one-row diagnosis
and batch workqueue exports agree. The template is a reviewer aid, not an
operation source. Current templates:
`facet_text_rewrite` for simple `uk_manual_frontier_heading_facet_candidate`
rows and `schedule_part_wrapper_insertion` for heading-frontier rows that
insert a schedule Part heading before an anchor paragraph plus its existing
italic heading,
`crossheading_text_rewrite` for `uk_manual_frontier_crossheading_candidate`,
`table_crossheading_text_rewrite` for
`uk_manual_frontier_table_crossheading_candidate`,
`schedule_note_text_rewrite` for `uk_manual_frontier_schedule_note_candidate`,
`schedule_list_entry_mutation` for
`uk_manual_frontier_schedule_list_entry_candidate`,
`table_surface_mutation` for the table-entry/table-column manual frontier
families,
`appropriate_place_mutation` for
`uk_manual_frontier_appropriate_place_candidate`,
`structural_sibling_insert` for
`uk_manual_frontier_structural_sibling_insert_candidate`,
`table_repeal_or_omission` for
`uk_manual_frontier_repeal_table_candidate`,
`amendment_program_target_mutation` for
`uk_manual_frontier_amendment_program_target_candidate`,
`source_carried_multi_subunit_text_rewrite` for
`uk_manual_frontier_source_carried_multi_subunit_text_rewrite_candidate`,
`source_carried_child_tail_text_rewrite` for
`uk_manual_frontier_source_carried_child_tail_text_rewrite_candidate`,
`source_carried_structured_text_patch` for
`uk_manual_frontier_source_carried_structured_text_patch_candidate`,
`definition_child_and_tail_substitution` for
`uk_manual_frontier_definition_child_and_tail_substitution_candidate`,
`definition_child_structural_substitution` for
`uk_manual_frontier_definition_child_structural_substitution_candidate`,
`structural_child_range_substitution` for
`uk_manual_frontier_structural_child_range_substitution_candidate`,
`definition_entry_insert` for
`uk_manual_frontier_appropriate_place_definition_entry_candidate`,
`index_entry_insert` for
`uk_manual_frontier_appropriate_place_index_entry_candidate`, and
`range_to_container_substitution` for
`uk_manual_frontier_range_to_container_candidate`. They list required
validator checks; replay must ignore them until a separate validated claim
ledger emits canonical operations and provenance.
`table_repeal_or_omission` templates also carry the blocking repeal-table
lowering rule/reason when available. For mixed structural-repeal and word-
omission table rows, they require a claim to split the structural repeal from
the text omission clauses instead of replaying the whole row as one mutation.
`amendment_program_target_mutation` templates also carry the parsed lowering
rejection fields (`source_target_address`, `source_subparagraph_label`,
`source_item_label`, `inserted_parent_label`, `insert_direction`,
`anchor_label`, `inserted_label`, and `inserted_text_preview`) because those
fields define the minimum auditable claim surface for compiling a mutation into
the payload of a prior amendment instruction. They are evidence, not replay
authority.
`definition_child_and_tail_substitution` templates carry the parsed definition
term, definition child label, trailing connective, and replacement preview for
sources that substitute a definition child together with the `and`/`or` tail at
the end of that child. They are intentionally non-executable until a claim or
future compiler owns both the definition-child text boundary and the
post-child-tail boundary.
`definition_child_structural_substitution` templates carry the parsed
definition term, definition child label, included trailing connective when the
source names one, and replacement preview for sources that substitute a
definition child with structural payload. They are intentionally non-executable
until a claim or compiler owns the replacement child shape and proves the
post-child-tail boundary.
`table_surface_mutation` templates carry `source_target_surface`,
`source_target_address`, and `table_entry_shape` when lowering has identified
a table-entry or column instruction but blocked replay for lack of a cell, row,
column, or ordering claim. In particular, `appropriate_place_table_entry`
requires an external ordering/anchor claim before it can become executable.
`uk-effects` summary output also aggregates
`suggested_claim_template_status_counts` for actionable frontier rows
(`manual_compile_candidate` and `deterministic_frontend_candidate`) so review
runs can distinguish template-ready rows from rows that still need a family
model. Source-insufficient, already-supported, and out-of-scope rows are not
counted as actionable template work.
The same actionable status is filterable with
`uk-effects --claim-template-status available|not_available`, including when
writing `--evidence-jsonl`; non-actionable rows do not match either value.
Archive-backed `uk-candidates` rows and summaries carry the same
`suggested_claim_template_status_counts`, and text summaries print it under
`manual_compile_frontier` as `claim_templates=...`. The
`uk-candidates --claim-template-status available|not_available` filter is an
archive-backed inspection filter: it emits statutes with at least one
actionable inspected effect row matching that template status. Saved-bench-only
rows cannot answer this because they do not retain the full per-effect witness
needed to determine template availability.

The claim output should be typed, for example:

```text
claim kind: semantic_compile
action: heading text_replace
target: section:10/heading
old: Public Standards Commissioner
new: Commissioner for Ethical Standards in Public Life in Scotland
source witness: ssi/2013/197 Sch. 2 para. 3
```

#### Rows that are not replayable without better source

These should remain blocked or classified until acquisition/extraction improves:

- missing extracted source payload where the public archive lane gives no
  instruction text or payload
- naked payload fragments such as a single body phrase with no action verb or
  anchor
- broad source fragments reused as payload where unrelated sibling content
  cannot be separated
- dot-leader or non-substantive shell payloads
- effect rows whose legal state change is not a text/tree mutation, such as
  transfer of functions or applied-with-modifications, unless LawVM adds a
  separate non-textual legal-state model

Manual claims may classify these as non-replayable from available public
surfaces, but they should not invent closed operations.

#### Validator obligations for UK claims

A UK manual claim validator should check at least:

- the cited source phrase exists in the archived affecting source or accepted
  reconstructed source
- the claimed action family matches the source verb/effect family
- the target address and target facet exist or the claim records a typed target
  gap
- old text exists in the claimed target text/heading/table cell when replacing
  or deleting
- inserted structural payload does not smuggle sibling or carried context
- table/repeal schedule claims identify the table row and column basis
- extent, commencement, and applied/unapplied status are preserved
- changed paths are limited to claimed target facets or declared migration /
  editorial projection paths

Accepted manual UK claims should emit operation provenance containing the claim
id, validator version, and source witness locator. Rejected claims should emit
typed `manual_compilation` observations, not disappear.

#### Replay Prepare Filter Contract

UK replay has a small pre-executor prepare phase for operations that should not
reach ordinary tree apply. That phase is still a compiler filter, so it must
preserve rejected operations as evidence.

Current rule:

- non-`repeal` `/whole_act` operations are not applied by the ordinary executor
- the prepare result must include the accepted operations and a typed
  `uk_replay_unsupported_action` adjudication for each rejected operation
- public replay paths should still append those adjudications to
  `adjudications_out` when a caller supplies one
- direct prepare-level tests should assert both the accepted-op list and the
  rejected adjudication payload so the filter cannot regress to accepted-only
  output

The strict disposition for this filter is `block`; quirks mode may continue
only with the rejected operation recorded.

Current schedule-reference invariant:

- bare schedule references like `Sch 4 para. 2` must normalize exactly like `Sch. 4 para. 2`
  - they are schedule-rooted targets, not `section:sch/...`
  - current example: `ukpga/2002/21` `Sch 4 para. 2` / `Sch 4 para. 8`
  - fixing that normalization moved `ukpga/2002/21` from `99.6%` to `99.7%`
    by removing the false `schedule-4-paragraph-2/8` replay tail

Current text-span invariant:

- `for the words from the beginning to "X" substitute "Y"` is a valid text-span
  replacement form and compiles to `TEXT_FROM__TO_X`
  - UK source also has the doubled formula `from the words from the beginning
    to "X" substitute "Y"`; this is the same bounded text-span family, not a
    permission to rewrite the whole subsection.
  - current example: `ukpga/2002/21` `s. 63(13)`
  - once parsed, it applies as a subtree text replacement on `section:63/subsection:13`
    and removes the last replay-only `section-63-13-a/b` tail

Current tooling-consistency invariant:

- `uk-effects` candidate gating must use the same effective target-shape facts as
  `uk-effect`
  - descendant EID presence counts as effective children even when the parsed node
    itself has no child IR nodes
  - current example: `ukpga/2002/21` `s. 28(1)`
  - without this, `uk-candidates --fast --residual-only` can resurrect fake residual
    frontier rows that the row-level inspector already classifies away
- `uk-effect`, `uk-effects`, and `uk-candidates` expose machine-readable
  frontier reports
  - report rows are diagnostic/evidence surfaces, not replay semantics
  - structural effects that lower to no replay operations must surface the same
    blocking `uk_effect_lowering_no_ops_rejected` record in both pipeline and
    inspection tooling, even when a more specific lowering rejection is also
    present
  - applicable nonstructural no-op rows also use the same no-op finalization
    path in pipeline and inspection tooling; narrow replay-candidate families
    such as `revoked` / `ceases to have effect` get
    `uk_effect_nonstructural_lowering_no_ops_rejected`, while other applicable
    non-commencement rows get
    nonblocking `uk_effect_nonstructural_unsupported_no_ops_observed`
  - `candidate: true` excludes rows with blocking lowering rejections; rejected
    lowering lanes remain visible as evidence rather than being counted as clean
    replay candidates
  - `uk-candidates` carries lowering-rejection counts separately from source and
    compare counts so blocked lowering lanes are not misread as generic
    classification-heavy residue
  - `uk-candidates` also partitions source/compare counts into candidate and
    non-candidate lanes; aggregate `source_counts` / `compare_counts` remain
    all-row inventory, not a claim that the residual-driving candidates have
    those pathologies
  - `uk-candidates` distinguishes saved bench `effect_count` from
    archive-backed `inspected_effect_count` because saved frontier rows can be
    stale relative to the local archive
  - `uk-candidates` also carries saved bench `effect_feed_page_count` alongside
    the legacy `effect_count` so frontier rows preserve the benchmark
    feed-page-vs-effect-row distinction
  - `uk-candidates` carries saved bench `effect_row_count` when present; core
    frontier classification should prefer parsed effect rows over archived
    feed-page counts because a malformed or empty feed page is source evidence,
    not an actionable replay effect
  - top-level `uk-candidates` summaries aggregate inspected effect counts,
    candidate/residual candidate counts, source/compare classification counts,
    lowering rejection counts, and residual-root category counts across emitted
    rows so agents do not need to infer the frontier shape by scanning every
    row first
  - top-level `uk-candidates` summaries must also expose saved benchmark
    `comparison_class` and core/non-core counts. Candidate triage is filtered
    through the core frontier, but summary-only reports should still prove which
    comparison classes survived that filter.
  - top-level `uk-candidates` summaries distinguish
    `matched_frontier_count` from `inspected_frontier_count`; `--top` is a
    diagnostic budget, and `frontier_truncated` must make that budget visible
  - each JSON row also carries the `score_mode` that produced its
    `frontier_score`, so row-level exports remain interpretable when copied out
    of the top-level report context
  - `uk-candidates --json --summary-only` emits only that aggregate surface,
    which is useful for batch dashboards and agent triage loops that do not need
    every per-statute row
  - `uk-candidates --json --summary-only --top 0` is a saved-run frontier
    dashboard mode: it emits no per-statute rows and does not inspect effects,
    but it must still aggregate saved benchmark provenance, manual-frontier
    counts, lowering observations, replay adjudications, and residual-claim
    tiers for all matched frontier rows
  - `uk-candidates --summary-count-limit N` is a JSON reporting budget for
    aggregate count maps. It requires `--json`, keeps the top N entries by
    count, and records omitted entry counts per map under
    `summary_count_map_omissions`; it must not change frontier matching,
    candidate selection, residual analysis, or saved evidence.
  - text `uk-candidates` output must print a compact aggregate summary sourced
    from the same summary fields as JSON, so interactive runs do not require
    manual row-scanning to see truncation, budget skips, candidate counts, and
    residual-root totals
  - text `uk-candidates` summaries must also print feed-parse,
    source-acquisition, lowering, and blocking-lowering rejection rule counts
    when present; row totals alone hide which source/replay family remains
    actionable
  - text `uk-candidates` budget summaries must distinguish rows with rejection
    evidence from total rejection records for feed-parse, residual-compile, and
    saved benchmark authority lanes; copied triage output must not require JSON
    reopening to see whether one row carries many source/replay failures
  - archive-backed `uk-candidates` rows and summaries must expose effect-feed
    parse/acquisition rejection counts separately from effect lowering/source
    classification counts; malformed feed pages are source-lane evidence, not
    absence of replay candidates
  - nonblocking effect-feed parse/acquisition rows are observations, not
    rejections. `uk-candidates` must preserve total feed observations and rule
    counts separately from blocking feed-parse rejection counts/rules.
  - archive-backed `uk-candidates` rows and summaries must also expose source
    acquisition rejection counts, such as missing affecting-act XML, separately
    from source-pathology classifications; derived classifications are not a
    substitute for the acquisition fact
  - saved-run `uk-candidates --fast` rows must preserve benchmark-level effect
    source-pathology counts and benchmark-level source-acquisition rejection
    counts/rules too. Fast mode is a prefilter; it must not imply the saved
    bench run had no missing affecting-act XML merely because effect inspection
    was skipped.
  - archive-backed `uk-candidates` rows expose a bounded
    `residual_candidate_samples` list for candidate effects that overlap
    replay/oracle residual roots; samples identify the source effect row and
    resolver/root overlap, but do not affect candidate counts or replay
    semantics
  - `uk-candidates --summary-only` is rejected without `--json` so the flag
    cannot silently degrade into ordinary text output
  - `uk-candidates --fast` prefilter rows must still preserve saved bench
    source status, byte size, source URLs, and replay regime. Fast mode skips
    effect inspection; it is not permission to drop benchmark provenance.
  - `uk-candidates --fast` prefilter rows must also preserve saved benchmark
    effect-feed, authority-filter, and lowering rejection counts/rules. The
    archive-backed inspector may refine those lanes later, but fast mode should
    not rewrite saved-run evidence to zero.
  - `uk-candidates --fast` prefilter rows must preserve saved benchmark
    source-parse observation/rejection counts/rules as well. A malformed
    available source XML blocker is benchmark provenance even when candidate
    analysis is intentionally skipped.
  - text `uk-candidates --fast` rows must print saved benchmark feed-parse,
    feed-observation, source-parse, authority-filter, lowering, and
    blocking-lowering rejection/observation rules when present; `(skipped
    --fast)` means no archive inspection, not no saved evidence.
  - text `uk-candidates` rows must label saved parsed effect-row counts
    separately from feed-page counts. The legacy saved `n_effects` field is a
    compatibility surface and must not be printed as if it were parsed replay
    effect inventory.
  - `uk-candidates --json --summary-only` must also aggregate saved benchmark
    effect inventory (`saved_legacy_effect_count`, `saved_effect_row_count`,
    `saved_effect_feed_page_count`) because row omission otherwise hides
    whether a fast frontier summary saw parsed effect rows or only feed pages.
  - `uk-bench --show` should keep aggregate count lines and aggregate score
    lines distinct; repeated labels such as two separate `Core benchmark rows`
    lines make copied benchmark reports harder to parse.
  - `uk-effects` text summaries should expose the same aggregate source
    pathology, compare-shape, resolver-hit, and lowering-row evidence as JSON
    summaries. Otherwise copied text summaries hide whether a candidate set is
    replay-ready, source-blocked, or merely unclassified.
- `uk-replay` text output should keep source URL labels mechanically
  copyable and consistent (`Enacted URL: ...`, `Oracle URL: ...`).
- `uk-effect` JSON source surfaces follow the same provenance contract as
  `uk-effects`: enacted/oracle source status, byte size, parse-failure flags,
  URLs, and SHA-256 hashes must travel together when archive bytes are present.
  - residual roots are split into replay-only and oracle-only root lists so a
    backed oracle-only omission is not confused with a replay-only surplus
  - malformed residual roots that preserve publisher residue, for example
    `section-1.`, are deferred under a named triage rule instead of being
    counted as normal defeated branches
  - `uk-effect` and `uk-effects` summary/JSON surfaces expose lowering
    diagnostics first as `lowering_observations`, with `lowering_rejection_*`
    retained as a compatibility alias for the same full diagnostic set.
    Blocking-lowering fields are the strict replay-blocking subset.
  - `uk-effects` summary separates all lowering observation counts from
    blocking lowering rejection counts
  - text `uk-effects` summaries must print blocking lowering rejection rules as
    their own block, matching the JSON distinction; otherwise a blocking
    lowering lane can be mistaken for ordinary unsupported residue
  - text `uk-effects` summaries must print the number of rows with blocking
    lowering rejections, not only the blocking rule histogram
  - text `uk-effects` summaries must also expose truncation, and text rows must
    expose the same replay applicability lanes as JSON (`requires_applied`,
    `metadata_only`, `replay_applicable`, and `structural_for_replay`)
  - `uk-effects` also separates matched effects before `--limit` from emitted
    rows after `--limit`; row-level classification counts remain scoped to the
    emitted rows
  - `uk-effects` JSON summaries expose `diagnostic_count_scope=emitted_rows`,
    and truncated text summaries print that scope, so a limited diagnostic
    sample is not mistaken for all matched effect coverage
  - `uk-effects --limit 0 --json --summary-only` is a valid empty diagnostic
    budget: matched counts survive, emitted rows and row-scoped classification
    lanes are zero, and `truncated` is true when matches existed
  - ordinary `uk-effects --limit` listings may pre-limit before expensive
    effect summarization, but `--candidate-only` / `--non-candidate-only` must
    classify the full matched set before applying `--limit`
  - `uk-candidates --fast --residual-only` must preserve rows skipped by
    `--residual-budget`, even when no candidate effects were found. A diagnostic
    budget skip is evidence about incomplete residual analysis, not proof that
    the row has no residual frontier.
  - `uk-candidates` residual compile reports follow the same split as replay:
    `residual_compile_observation_*` carries all residual feed/lowering/authority
    rows, while `residual_compile_rejection_*` is blocking-only. Rows without an
    explicit `blocking` key remain blocking for legacy safety.
  - diagnostic row/corpus limits must reject negative values rather than
    treating Python negative slicing as a hidden evidence filter
  - `uk-candidates --top` is a diagnostic row budget and must also reject
    negative values before loading a saved run; `--top 0` is the explicit empty
    frontier
  - diagnostic tools that limit emitted rows must preserve pre-limit match
    counts and expose truncation instead of relabeling emitted rows as matches
  - `uk-eids` JSON and text output must carry side-level source URLs, source
    SHA-256 identity, and the archive path so base/oracle missing-lane
    diagnosis is self-contained and reproducible
  - `uk-eids` source surfaces must distinguish absent archive entries from
    suspiciously small cached blobs. Both remain `missing=true` for
    compatibility, but `source_status` / `source_size` are the evidence fields
    used for acquisition diagnosis.
  - `uk-eids` must also classify available-but-unparseable source XML with the
    same `uk_enacted_xml_parse_rejected` / `uk_oracle_xml_parse_rejected`
    source-pathology records used by replay/evidence/bench. It may keep
    `missing=true` because no EID rows can be emitted, but parse failure must
    be typed evidence in JSON and text output.
  - `uk-eids` source-parse evidence follows the shared UK observation/rejection
    split: `source_parse_observation_*` carries all parse records, while
    `source_parse_rejection_*` is blocking-only under the shared compile-record
    classifier. A `strict_disposition=record` source-parse row is visible
    evidence, not a source-unavailable failure.
  - `uk-replay --json` must carry the enacted/oracle source URLs and normalized
    EID comparison counts/samples (`replay_compare_eid_count`,
    `oracle_compare_eid_count`, `only_in_*`) so benchmark triage does not depend
    on scraping human text output
  - `uk-replay --json` source payloads must also include SHA-256 hashes for
    archived enacted/oracle XML bytes when the archive entry exists, even if the
    blob is too small or later parse-rejected. Absent archive entries carry no
    hash.
  - `uk-replay --enacted-only --json` still loads the archive effect feed for
    effect counts, so it must thread effect-feed parse/acquisition rejections
    into the JSON compile-rejection lane rather than treating baseline mode as
    evidence-free
  - `uk-replay --json` must emit a machine-readable source payload before
    failing on missing or too-small enacted XML. The command may still exit
    nonzero, but source status, source URLs, archive path, replay regime, and
    oracle source status must not be stderr-only evidence.
  - `uk-replay --fetch-missing --json` must include the
    `UKPrefetchReport.to_dict()` payload under `uk_prefetch_report`. Prefetch
    acquisition failures are source-lane evidence and must not remain stderr-only
    when replay continues.
  - UK affecting-act prefetch also depends on the effect feed before it can know
    which affecting acts are missing; feed parse/acquisition rejections must be
    threaded into `UKPrefetchReport.events` and blocking feed failures must
    contribute to `error_count` instead of being reported as simply no
    structural effects
  - `UKPrefetchReport.to_dict()` must expose event counts and rule counts for
    all acquisition events and blocking acquisition events. Legacy feed rows
    without an explicit `blocking` key count as blocking; explicit
    `blocking=false` remains a nonblocking observation.
  - UK affecting-act prefetch success is also source-lane evidence. Cached and
    newly fetched affecting-act XML must emit nonblocking source-witness events
    with locator, byte length, and SHA-256, so successful acquisition is no less
    auditable than permanent-missing or network-failure paths.
  - UK affecting-act prefetch can also fetch the enacted affecting-source lane
    with `--include-enacted-affecting`. This stores
    `/{affecting_act_id}/enacted/data.xml` for cached or newly fetched current
    affecting acts so source-lane selection rules such as
    `uk_affecting_act_current_shell_enacted_source_selected` are reproducible
    from ordinary prefetch workflows, not only from a full corpus acquisition.
  - UK affecting-act prefetch must use the same source-state availability
    classifier as replay/effect/bench surfaces. The too-small threshold belongs
    in `source_state`, not a local prefetch byte-count heuristic.
  - UK affecting-act prefetch dry-runs are also acquisition evidence. Missing
    affecting-act XML that would be fetched must emit nonblocking
    `uk_prefetch_affecting_act_would_fetch` source-witness events with statute
    id, affecting act id, URL, and locator; dry-run summaries without row
    events are not sufficient source evidence.
  - Batch and CLI prefetch text output must print acquisition event rule counts
    and blocking event rule counts when present. Event JSON/JSONL is not enough
    for interactive source-acquisition triage.
  - If an effect compiles to operations but the replay applicability regime
    excludes that effect before replay apply, the compile diagnostics must emit
    `uk_effect_replay_applicability_filter_rejected` with effect id, compiled op
    ids/actions, structural/replay-applicable flags, and strict/quirks
    disposition. Compiled-then-filtered operations are rejected source/effect
    lanes, not invisible absence.
  - PIT-date effect selection must also be visible. Effects whose selected
    effective date is later than the requested point-in-time date must emit
    nonblocking `uk_effect_pit_date_filter_rejected` diagnostics with effect id,
    effective date, PIT date, target/source provision strings, and
    strict/quirks disposition. Future effects are expected to be excluded, but
    they are still source lanes that must not disappear silently.
  - Source-pathology filters that block already-compiled operations must run
    before manual-frontier classification for that row. In particular,
    `instruction_text_reused_as_payload` plus a blocking
    `uk_effect_instruction_text_payload_rejected` row is source-insufficient
    evidence, not a deterministic supported/manual-claim candidate.
  - `uk-effect`/`uk-effects` source-pathology summaries must use the same
    replay-regime-aware structural flag as compile/replay
    (`is_structural_for_replay(applicability_mode=...)`), not the raw feed
    `is_structural` property. Tooling summaries must not disagree with replay
    when an alternate applicability mode admits or excludes an effect row.
  - UK candidate effect inspection must not be an accepted-only filter. Rows
    excluded before effect summarization because of metadata-only policy, replay
    applicability, or effect-budget truncation must be available as
    `effect_selection_observations` on candidate rows with stable rule ids.
    These observations do not change candidate counts or residual row inclusion;
    they explain why an effect was not inspected.
  - `uk-candidates` text summaries must also aggregate and print
    effect-selection observation/rejection rule counts. JSON-only visibility is
    not enough for copied triage summaries.
  - UK bootstrap `.meta.json` files written for manifest artifacts and effect
    feed pages must include `sha256` beside requested URL, final URL, and byte
    length. Fetch metadata is a source witness, not just a download log.
  - `uk-bench --compare` must print the primary score mode used for each saved
    run (`raw`, `commencement`, or `mixed`) and the number of statutes present
    only on each side, because saved CSVs may use commencement score as the
    primary `score` column while retaining raw EID score as `raw_score`
  - UK bench commencement scoring is available only when the commencement lane
    produces at least one commenced EID for the statute. An empty commenced
    set is "not computed", not a zero-score primary headline; otherwise small
    samples with no commencement evidence hide the raw replay/oracle signal.
  - UK commencement EID matching is a temporal/applicability comparison lane,
    not structural replay. It must match enum-backed IR nodes by normalized
    legal kind, descend through structural containers when a section-level
    commencement target is named under a part/chapter/crossheading, consume
    named schedule roots when present, and bubble commenced descendants to
    their structural ancestors so oracle-visible parent EIDs are counted. Under
    the default replay lens it must also respect feed applicability
    (`effective_date_plus_feed_applied`) instead of treating unapplied
    commencement rows as current law merely because they carry a date.
  - UK commencement metadata may name an unnumbered schedule target, e.g.
    `Sch. para. 18`. LawVM may resolve this only when the enacted source has
    exactly one schedule root, recording
    `uk_commencement_unnumbered_single_schedule_target_resolved` as
    `target_resolution_recovery`. Multiple schedule roots remain unresolved
    until source context disambiguates the target.
  - UK current XML may expose provisions that are present in the instrument but
    not yet commenced. Commencement EID scoring is therefore symmetric: compare
    commenced enacted/replay EIDs against oracle EIDs intersected with the same
    commenced EID set. Raw EID scoring remains the unfiltered current-XML
    comparison lane.
  - `uk-replay --commencement` exposes that same symmetric commencement
    comparison for one-statute diagnosis. It is an additional temporal
    comparison lane, not a replacement for the default raw EID score, and its
    JSON/text evidence must preserve commencement-filter observations such as
    undated commencement blockers.
  - `Appointed Day(s)` is a commencement-like effect type. If a statute has
    commencement-like rows but none has a replay-applicable effective date,
    LawVM must not fall back to whole-instrument self-commencement. It returns
    no commenced EIDs for that lane and records
    `uk_commencement_undated_effects_block_self_commencement` as
    `temporal_recovery`; raw scoring remains available as the fallback
    comparison.
  - `uk-bench --compare` must also summarize saved enacted/oracle and
    replay/oracle text-score fields over common statutes when present. EID
    agreement and text agreement are different evidence lanes.
  - `uk-bench --compare` top regression/improvement rows must include compact
    row evidence: status, comparison class, source statuses, replay regime,
    ops, rejection counts, and replay adjudication count. Score deltas copied
    without evidence context are not actionable triage records.
  - That compact row evidence must include replay-time effect source-pathology
    counts, not just blocking source-acquisition counts. A copied top-row line
    should distinguish missing extracted source, nonstructural root gaps, and
    clean/no-pathology rows without reopening the CSV.
  - `uk-bench --show` top replay regression/improvement rows must include the
    same compact row evidence. Immediate run output is also a copied triage
    surface, not just a score summary.
  - compact `uk-bench` row evidence must include source byte sizes and URLs in
    addition to source statuses. `available` vs `too_small` vs missing source
    diagnosis should not require reopening the saved CSV.
  - compact `uk-bench` row evidence must include observation counts as well as
    rejection counts for source-parse and effect-feed lanes. Observation-only
    source evidence should remain visible in top regression/improvement rows.
  - `_score_statute` broad row-level failures must emit
    `uk_bench_unclassified_exception` benchmark-execution observations when the
    exception was not already classified by a narrower source-parse lane. Batch
    isolation is allowed; untyped `ERR` rows are not.
  - Replay errors inside `_score_statute` must not erase phase-local diagnostics
    already emitted by UK compile. If compile/replay fails after appending
    authority observations, lowering rejections, effect source-pathology rows,
    manual-frontier rows, or affecting-act source-acquisition rows, the returned
    benchmark row must still aggregate those counts/rules alongside
    `replay_error`. Failure to materialize replay state is not permission to
    discard earlier source/effect evidence.
  - saved UK bench CSVs, history output, and run comparisons must persist
    `uk_bench_unclassified_exception` counts, rule IDs, and row observations.
    A typed batch-isolation failure is still evidence; it must survive save/load
    rather than being visible only in the immediate run.
  - `uk-candidates` must also preserve saved `uk_bench_unclassified_exception`
    counts, rule IDs, and observation rows in JSON rows, aggregate summaries,
    and text rule blocks. Candidate triage often starts from saved bench rows;
    a benchmark-execution failure must not disappear just because effect
    inspection is skipped.
  - `uk-candidates` saved-run rows must preserve rehydrated
    `<label>.diagnostics.jsonl` records as `saved_bench_diagnostics`, with
    aggregate rule and lane counts in row JSON and report summaries. Fast
    prefilter mode may skip archive inspection, but it must not discard the
    saved benchmark sidecar evidence. Fast text rows must also print the saved
    diagnostic rule and lane counts; copy-paste triage should not require JSON
    just to see which phase-local sidecar records were preserved.
  - saved UK bench runs may write a bounded `<label>.score_witnesses.csv`
    sidecar. The main CSV remains the compatibility score table; the sidecar
    preserves deterministic sampled EID mismatches by score scope (`raw`,
    `replay`, `commencement`, `replay_commencement`) with source status, source
    URLs, replay regime, category totals, sample limits, truncation flags,
    side labels, schema, and the explicit score formula. Do not put legal text
    excerpts in this sidecar.
  - saved UK bench runs may also write a `<label>.diagnostics.jsonl` sidecar for
    row-level diagnostic records that are too structured for the compatibility
    CSV. The sidecar uses `uk_bench_diagnostic.v1` rows keyed by label, statute
    id, diagnostic lane, row index, rule id, blocking flag, and the original
    typed diagnostic record. At minimum it preserves source-parse observations,
    effect-feed parse/acquisition observations, source-acquisition diagnostics,
    effect-source-pathology diagnostics, manual-compile-frontier diagnostics,
    fallback effect diagnostics, authority observations/rejections, lowering
    rejections, replay adjudications, and benchmark-execution observations when
    those records are available on `_BenchResult`. `_load_run()` must read this
    sidecar back into `_BenchResult`; a saved run that rehydrates only CSV
    counts but drops row-level records has not preserved the evidence.
  - `uk-bench --show`, `uk-bench --compare`, and save output should make this
    sidecar discoverable by printing its path and row count when relevant. A
    hidden sidecar is not useful interactive audit evidence.
  - saved UK bench CSVs may persist measured phase timings as compatibility CSV
    columns (`phase_total_s` and `phase_<name>_s`). These timings are operational
    evidence, not legal evidence: they explain benchmark/runtime regressions
    without changing score semantics. `uk-bench --show <label> --phase-timings`
    must read saved timing columns back and print the slowest phase rows, or
    explicitly say that the saved run has no measured phase timings. `uk-bench
    --compare` should report aggregate phase-time drift when both saved runs
    contain timings for common statutes.
  - saved UK bench CSVs may also persist `process_maxrss_kb`, the process
    high-water resident set size observed after that row. This is operational
    evidence for full-corpus reliability and WSL2/OOM diagnosis, not legal
    evidence. It is process-scoped and may carry an earlier peak forward within
    a reused worker, so text reports should describe it as RSS pressure evidence
    rather than exact per-statute allocation.
  - UK replay performance work should be phase-evidence-led. A 2026-05-23
    `ukpga/1988/1` replay witness measured roughly 62s wall time with
    `compile_ops` and `replay` each around 29s. Parent-process `cProfile` with
    multiprocessing mostly measured process-pool waiting; use `--parallel 1`
    or per-worker profiling for useful call stacks. A speculative unique
    EID-sequence lookup index did not materially improve this witness and should
    not be retried without hit-rate evidence showing sequence-alias lookup misses
    dominate a different corpus slice.
  - A 2026-05-23 serial profile for `ukpga/2021/26` showed a different
    `compile_ops` hotspot: a source-payload trailing double-comma cleanup regex
    consumed roughly 109s of a 114s run through repeated `re.Pattern.search`
    calls. That cleanup is now a linear string suffix normalizer; the same
    witness measured 3.42s wall time with `compile_ops=0.43s`. Future perf work
    should treat anchored regexes with leading `\s*` over large XML payload text
    as suspect until profiled.
  - Existing 2026-05-23 profiles show at least two separate remaining slow
    families. `ukpga/1988/1` is replay-side heavy: invariant scans, target
    lookup, insert/repeal apply, and recursive kind/label matching dominate.
    `ukpga/1990/42` is compile-side heavy: repeated affecting-source context
    selection and provision extraction dominated before replay. The `1990/42`
    source-extraction family now uses first-component matches to search each
    candidate subtree instead of falling back to a full-source greedy walk when
    the first component is ambiguous; the serial witness measured 6.48s wall
    time with `compile_ops=4.30s` after this change, versus a prior 55.8s
    profile with `compile_ops` around 45.9s. First-component lookup now builds
    a per-source-root normalized-number index and then filters kind synonyms in
    document order; this keeps the same extraction semantics while avoiding a
    repeated full-tree scan for every distinct first component. The same
    `1990/42` witness measured `compile_ops=4.18s` after the index, and later
    `compile_ops=4.09s` with replay score unchanged at 92.9%. Replay-side
    `1988/1` remains a separate indexing/cache family; a minor invariant-scan
    hot-path cleanup kept the fast duplicate/order scan equivalent to the
    generic invariant subset and measured 60.81s wall time with `replay=27.73s`
    on that witness, so it did not remove the need for deeper replay-side
    indexing. A rejected global parent-lookup index was score-equivalent but
    slower because structural replacements rebuilt the index too often. The
    accepted replacement hot path reuses the existing exact `eId` lookup index
    only when it already records the node's parent tuple, avoiding new whole-tree
    indexing; the `1988/1` witness stayed at 99.6% replay and measured 55.86s
    wall time with `replay=23.66s`. A follow-up invariant-diagnostics gate skips
    payload-shape invariant checks when the scoped tree scan found no new
    violation; it is score-equivalent and measured 55.71s wall time with
    `replay=23.01s` on the same witness. Reusing the cached core kind-string
    helper inside the duplicate/order scan removed a profiled enum/getattr hot
    helper while preserving the emitted invariant subset; the same witness
    measured 54.11s wall time with `replay=22.47s`. Caching the UK kind-value
    normalizer removes another profiled enum/string normalization hot helper
    used by target matching; the same witness measured 54.04s wall time with
    `replay=22.29s`. Scoped unique `eId` suffix aliases are now indexed only
    when unambiguous within their top-level `section-N`/schedule-like scope,
    avoiding recursive fallback for payload/source IDs that carry a harmless
    prefix while preserving ambiguous cases as ordinary slow-path lookups. The
    `1990/42` witness remained score-equivalent at 92.9% replay and measured
    6.59s wall time with `replay=0.40s`; the `1988/1` witness remained
    score-equivalent at 99.6% replay and measured 55.45s wall time with
    `replay=23.08s`. The invariant scan now relies on the UK mutable tree's
    typed `IRNodeKind.value` directly instead of calling the generic kind
    normalizer for every child visited by duplicate/order diagnostics; this is
    score-equivalent and measured 53.20s wall time with `replay=21.55s` on the
    `1988/1` witness, and 6.37s wall time with `replay=0.38s` on the `1990/42`
    witness. Recursive kind/label target matching now skips recursion into leaf
    children after checking the leaf itself; this is score-equivalent and
    measured 52.52s wall time with `replay=20.84s` on `1988/1`, and 6.39s wall
    time with `replay=0.37s` on `1990/42`. Provision-reference normalization
    now strips non-alphanumeric characters with the existing non-alnum regex
    instead of collecting every alphanumeric character with `findall`; this
    preserves exact-id normalization semantics while reducing source-index build
    overhead. The witnesses remained score-equivalent: `1988/1` measured 52.36s
    wall time with `compile_ops=27.07s`, and `1990/42` measured 6.29s wall time
    with `compile_ops=3.91s`. UK `eId` sequence tokenization now replaces `_`
    with `-` and uses direct string splitting instead of the equivalent
    `[-_]` regex split; the boundary semantics are unchanged, including empty
    components, and a regression pins hyphen/underscore and roman-label
    normalization. The witnesses remained score-equivalent: `1988/1` measured
    52.76s wall time with `compile_ops=26.99s` and `replay=21.41s`, and
    `1990/42` measured 6.24s wall time with `compile_ops=3.93s` and
    `replay=0.36s`. Recursive `eId` fallback search now computes the `-eid`
    and `_eid` suffix strings once per lookup and carries them through
    recursion instead of rebuilding them for every child candidate; this keeps
    the exact suffix-match semantics and only removes repeated string work.
    The witnesses remained score-equivalent: `1988/1` measured 51.82s wall
    time with `compile_ops=26.76s` and `replay=20.76s`, and `1990/42` measured
    6.30s wall time with `compile_ops=3.94s` and `replay=0.38s`. A local
    duplicate/order invariant optimization skips post-repeal rescans only when
    the scoped tree had no previously seen invariant violation, because pure
    deletion cannot create duplicate labels or child-order inversions. If a
    scoped violation was already seen, repeal still rescans so stale findings
    can be cleared. The witnesses remained score-equivalent: `1988/1` measured
    51.77s wall time with `compile_ops=26.80s` and `replay=20.66s`, and
    `1990/42` measured 6.13s wall time with `compile_ops=3.82s` and
    `replay=0.35s`. The duplicate/order scan now streams adjacent child-order
    checks while it walks siblings instead of first materializing per-kind label
    and sort-key arrays; this preserves the emitted generic-invariant subset
    and measured `1988/1` at 51.21s wall time with `replay=19.84s`, and
    `1990/42` at 6.23s wall time with `replay=0.35s`. Cached suffix `eId`
    lookup is now also verified against the exact top-scope subtree before a
    top-scoped target is accepted; this fixes a target-hijacking risk where a
    source `eId` ending in the same suffix but living under another section
    could satisfy a strict target. The same witnesses stayed score-equivalent:
    `1988/1` measured 50.39s wall time with `compile_ops=26.55s` and
    `replay=19.61s`, and `1990/42` measured 6.17s wall time with
    `compile_ops=3.83s` and `replay=0.35s`. A local improvement in one family
    should not be treated as global UK replay progress without saved-run guard
    evidence.
  - saved UK bench CSVs must persist replay and commencement error lanes
    (`replay_error`, `commencement_error`) even when every replay/commencement
    attempt fails; stderr-only errors are not sufficient evidence
  - saved UK bench CSVs must persist parsed effect-row counts and effect-feed
    parse/acquisition observation counts alongside the legacy feed-page
    `n_effects` column; benchmark triage classifications use parsed effect rows,
    while feed-page counts remain a compatibility/source-inventory surface
  - saved UK bench and candidate reports must preserve `effect_feed_count_error`
    as human-readable source-acquisition evidence. Rule counts identify the
    family; the error string explains the failing witness lane when copied into
    triage.
  - Commencement scoring reloads the effect feed under a different downstream
    use, but feed parse/acquisition observations emitted before a commencement
    failure remain effect-feed evidence. If commencement loading fails after
    appending parse observations, `_score_statute` must merge those observations
    into the saved effect-feed observation/rejection counters alongside the
    `commencement_error` lane.
  - saved UK bench CSVs, history rows, show output, and run comparisons must
    preserve nonblocking effect-feed observation rule counts separately from
    blocking feed-parse rejection rules. Observation totals without rule IDs are
    insufficient source-lane evidence.
  - saved UK bench CSVs, history rows, show output, and run comparisons must
    also preserve available-but-unparseable enacted/oracle source XML as a
    `source_parse` evidence lane. The row may remain `ERR` for CSV compatibility,
    but it must carry `uk_enacted_xml_parse_rejected` /
    `uk_oracle_xml_parse_rejected` observation and blocking-rejection counts so
    malformed cached source is not confused with a programming exception or with
    absent/too-small acquisition.
  - `uk-bench --show` must print source-parse observation totals/rules as well
    as blocking source-parse rejection totals/rules. Some source-parse evidence
    may be nonblocking in future; observation lanes must not be inferred from
    rejection lanes.
  - saved UK bench CSVs, history rows, show output, and run comparisons must
    also preserve replay-time effect source-pathology classifications and
    blocking source-acquisition rejections such as missing affecting-act XML.
    `missing_extracted_source` is a derived pathology; it must not erase the
    separate `uk_affecting_act_xml_missing_rejected` acquisition fact.
  - `uk-bench --show` must print effect-feed observation/rejection totals across
    all rows before the no-OK early return. Source-unavailable and error rows
    can still carry feed evidence.
  - `uk-bench --show` must print authority, lowering, blocking-lowering, and
    replay-adjudication evidence across all rows before the no-OK early return.
    Replay evidence on `ERR` rows must not depend on having replay-scored OK
    rows in the same run.
  - UK bench history must aggregate replay regime counts across all rows, not
    only OK rows. A source-failed or error-only run still has configuration
    evidence.
  - `uk-candidates` replay-regime summaries must include every saved bench
    replay axis, including `metadata_only_effects`. Candidate triage must not
    collapse source-backed and metadata-only effect-selection regimes into the
    same summary key.
  - `uk-candidates` residual analysis must also execute under every saved bench
    replay axis. A row saved with `metadata_only_effects=0` must filter
    metadata-only effect rows and pass `allow_metadata_only_effects=False` when
    recompiling residual operations.
  - `source_first_candidate` and `source_semantics_clean` require
    `metadata_only_effects=0`. A run with metadata backfill disabled,
    oracle alignment disabled, and `authority_mode=source_text_only` is still
    not source-first if metadata-only effect rows are admitted into replay
    selection.
  - `uk-bench` effect-feed parse/acquisition rows without an explicit
    `blocking` key are legacy blocking rejections. Only explicit
    `blocking=false` feed rows are nonblocking observations.
  - saved UK bench history rows must also preserve effect-feed rejection rules,
    authority rejection rules, lowering rejection rules, blocking-lowering
    rejection rules, and replay adjudication kinds. `uk-bench --history` should
    render those lanes in a compact human-readable form while retaining legacy
    history compatibility.
  - saved UK bench history rows must preserve row-status and enacted/oracle
    source-status histograms across all rows, not only OK/core score rows.
    `n_total` without these distributions hides whether a run was replay-poor
    or source-acquisition poor.
  - UK bench history must still append a row when there are zero OK statutes.
    All-source-failed runs are benchmark evidence; they should carry blank
    score averages, `score_mode=none`, and source/row-status histograms.
  - when appending current-schema history rows to an existing legacy history
    file, `uk-bench` writes one current header segment and then appends current
    rows under that segment. Repeating the current header for every save hides
    whether rows belong to one benchmark era or many.
  - UK replay benchmark scoring may run the post-replay oracle EID alignment
    adapter. That adapter must expose `uk_oracle_eid_alignment_adapter` counts
    for changed EIDs, oracle-assigned EIDs, and local fallback EIDs so replay
    scores are not mistaken for source-pure EID output
  - UK bench saved-run reports and run comparisons must surface the oracle
    alignment adapter's before/after node counts, not only changed-EID method
    counts. A label adapter that changes identity but preserves tree
    cardinality is a different diagnostic fact from one that masks a node-count
    mismatch.
  - oracle EID alignment reports should also carry match-method provenance
    (`hash`, `fuzzy`, `flat`, `ordinal`, `local_fallback`, and
    `transparent_wrapper_cleared` where available); count-only reporting is not
    enough to distinguish source-pure matches from oracle-assisted scoring
- `uk-bench --show` / saved-run reporting must print persisted source-status,
  row-status, comparison-class, core-benchmark, replay-regime, effect-feed
  rejection, lowering rejection, oracle-alignment, and replay/commencement error
  lanes for otherwise-OK rows. These are diagnostic lanes and should not be
  hidden behind the top-level statute parse/acquisition `Errors` block or lost
  during CSV save/load.
- Effect-feed count failures in `uk-bench` must persist both the
  `uk_effect_feed_count_error` rule counts and the exception summary. A saved
  row that only records a synthetic count cannot explain whether the blocker was
  parse, acquisition, or a programming exception.
- UK bench history diagnostic rule aggregates must include non-OK rows, not only
  rows with scored oracle EIDs. A failed replay/acquisition row may still carry
  effect-feed, lowering, authority, or replay-adjudication evidence that belongs
  in the run-level ledger.
- `uk-bench --show` worst core/non-core row blocks must include row-level
  source status, byte size, and source URLs. These rows are commonly copied into
  follow-up triage, so aggregate source counts are not enough evidence.
- `uk-bench --show` replay-error and commencement-error blocks must also include
  row-level source status, byte size, and source URLs. Runtime failures against
  missing/suspicious source are a different diagnosis from failures against
  available source.
- UK bench text reports must list `NO_ENACTED` / `NO_ORACLE` rows with
  enacted/oracle source status and byte size. Source-acquisition failures are
  benchmark evidence, not just rows removed from the OK score denominator. When
  source URLs are present, those row-level diagnostics must print them too.
- UK bench text reports must also list `ERR` rows before the no-OK early return,
  with enacted/oracle source status and byte size. A parser/replay exception
  with cached source present is a different diagnosis from a source-acquisition
  failure. When source URLs are present, those row-level diagnostics must print
  them too.
- UK bench parse exceptions against source that already passed availability
  classification must emit source-pathology observations/rejections before
  falling into the row-level `ERR` lane. The exception summary remains useful,
  but the stable rule ID is the evidence used by history, show, compare, and
  downstream triage.
- Saved UK bench rows must persist enacted/oracle source URLs, not only source
  status and byte size. Downstream `uk-candidates` rows should be copyable as
  source-identifying evidence without re-deriving URLs from statute IDs.
- Saved UK bench rows and score-witness sidecars must also persist
  enacted/oracle source SHA-256 hashes when archive entries exist. Status, byte
  size, URL, and hash are the minimum source-identity tuple for later audit.
- Human-readable UK bench row evidence, source-unavailable rows, and error rows
  must print those source hashes when present. Text reports are copied into
  triage notes, so source identity cannot be JSON/CSV-only.
- UK bench corpus CSVs must also persist the exact enacted/current archive
  locators from corpus indexing. Reconstructing canonical URLs during corpus
  load can erase the actual fetched source lane before the benchmark row is
  even produced.
- UK bench corpus CSVs must likewise persist enacted/oracle source SHA-256
  hashes so a corpus manifest identifies the exact archive bytes used to seed
  later benchmark rows.
- saved UK bench CSVs must distinguish `n_effect_feed_pages` from the legacy
  compatibility field `n_effects`; until parsed effect-row counts are added
    to the corpus index, both values may be equal, but reports must label the
    count as effect-feed pages rather than parsed replay effects
  - CLI integration tests should pin helper-level diagnostic-budget contracts
    where ordering matters, especially `uk-effects --candidate-only --limit N`,
    `uk-effects --limit 0 --json --summary-only`, `uk-candidates --top 0`, and
    `uk-eids --limit 0 --json`
  - `uk-candidates --fast --residual-only` requires an archive DB because the
    residual-only claim needs archive-backed replay/oracle residual analysis
  - `uk-candidates --effect-budget N` is an explicit diagnostic budget for
    archive-backed triage over replay-applicable effects, including
    metadata-only rows that replay can consume; rows and summaries must
    expose `effect_inspection_truncated` / `rows_with_effect_inspection_truncated`
    so partial inspections are not mistaken for complete evidence
  - `uk-candidates` keeps `available_applied_effect_count` for compatibility,
    but budget truncation is governed by `available_replay_applicable_effect_count`
    because feed-applied status is narrower than replay applicability
  - `uk-effect` and `uk-effects` rows expose `metadata_only`,
    `replay_applicable`, and `structural_for_replay` so single-row/list
    inspection uses the same applicability lane as replay and candidate triage
  - `uk-effect --json` missing-effect failures must emit a typed JSON error
    bundle before exiting non-zero, including loaded effect count and feed
    parse/acquisition observation and blocking-rejection lanes. Missing effect
    IDs are diagnostics, not stderr-only failures.
  - `uk-effect` and `uk-effects` must treat available-but-unparseable enacted
    or oracle XML as source-lane parse rejections, not command-level crashes.
    The shared rule IDs are `uk_enacted_xml_parse_rejected` and
    `uk_oracle_xml_parse_rejected`; rows carry source URL, side, exception
    type/message, `blocking=true`, `strict_disposition=block`, and
    `quirks_disposition=record`.
  - `uk-replay` and UK evidence bundles use the same source-parse lane. A
    malformed enacted source blocks replay/evidence with a typed bundle; a
    malformed oracle source degrades replay to no-oracle comparison evidence
    with `oracle_xml_parse_rejected` as the oracle-alignment unavailable reason.
  - `uk-effects` JSON summaries expose source-surface provenance (`archive_path`,
    enacted/oracle URLs, and missing booleans) before compare-shape conclusions,
    because missing enacted/oracle lanes are source facts rather than replay
    outcomes
  - replay-vs-oracle EID comparison normalizes known collapsed oracle roots
    only as a benchmark surface rule, not as replay mutation semantics.
    Collapsed root handling includes sections, articles, crossheadings, and
    schedules when the oracle exposes only the root EID and replay has multiple
    descendants under that same root.
    - current witness: `asp/2003/17`, where current XML exposes Schedule 2 as
      `schedule-2` while replay/source parsing carries `schedule-2-paragraph-*`
      descendants
  - `uk-effects` text summaries must print the archive path and enacted/oracle
    source URLs alongside source status/size, so copied human triage preserves
    the same source surface as JSON.
  - `uk-effects` source summaries use the same `source_status` vocabulary as
    `uk-eids`: absent, too-small, or available. A too-small cached XML witness
    is not the same source state as an unfetched archive entry.
  - `uk-effects` JSON and text summaries expose archive-backed effect-feed
    parse/acquisition rejections separately from lowered effect rows; malformed
    feeds and indexed-but-missing payloads are source-lane evidence, not empty
    effect sets
  - `uk-effects` must split nonblocking effect-feed observations from blocking
    effect-feed parse/acquisition rejections. A feed observation such as an
    absent optional page is still source evidence, but it must not be reported
    as a replay-blocking rejection.
  - `uk-effects` text summaries must also print source-acquisition rejection
    rule counts from inspected rows, such as missing affecting-act XML. JSON-only
    visibility is not enough for interactive source-lane triage.
  - `uk-effects` text rows must also print per-row source-acquisition rejection
    rule counts. Aggregate summaries prove the family exists, but copied
    individual rows need to preserve which acquisition fact blocked that effect.
  - `uk-effects` text rows must split blocking lowering rejection rule counts
    from total lowering rejection rule counts. Row snippets copied from a
    listing must preserve the candidate-blocking fact without requiring JSON.
  - `uk-effect` single-row inspection must thread the same archive-backed
    effect-feed parse/acquisition rejection lane into JSON and text output so a
    chosen effect is not inspected against an invisible partial feed load
  - `uk-effect` single-row inspection must also split nonblocking feed
    observations from blocking feed rejections, matching `uk-effects` and
    `uk-candidates`
  - `uk-effect` text output must split blocking lowering rejection counts from
    ordinary lowering rejection counts, matching JSON and the multi-row
    `uk-effects` summary. Blocking no-op lanes decide replay-candidate status;
    hiding them inside total lowering counts makes row triage ambiguous.
  - `uk-effect` single-row inspection must also expose the archive path and
    enacted/oracle source URLs because its compare/source classifications depend
    on those surfaces, not only on the extracted affecting-act fragment
  - `uk-effect` source summaries use the same absent / too-small / available
    source-status vocabulary as `uk-eids` and `uk-effects`, and too-small XML
    blobs must not be parsed as valid enacted/oracle witnesses.
  - missing affecting-act XML must be emitted as
    `uk_affecting_act_xml_missing_rejected` in single-effect and list-effect
    source acquisition lanes; `missing_extracted_source` is a derived source
    pathology, not a substitute for the acquisition fact
  - local on-disk effect feed parsing uses the same parse/acquisition rejection
    families when a rejection sink is supplied; legacy no-sink parsing keeps its
    old behavior, but replay/evidence paths should thread the sink
  - archive-backed effect loading must record absent effect-feed page locators
    as `uk_effect_feed_pages_absent_recorded` in the same evidence lane; an
    absent feed and an empty parsed feed are different source states
  - UK evidence bundles expose `uk_oracle_alignment_summary` under compiler
    observations when oracle alignment is allowed; source-first/strict-style
    interpretation should treat this as an adapter lane, not as source-derived
    replay truth
  - UK evidence bundles expose `uk_compile_rejection_summary` under compiler
    observations, including blocking effect-feed parse/acquisition rejections
    and blocking lowering rejections with rule counts; authority rejection
    evidence alone is not a complete compile ledger
  - UK evidence bundles also expose `uk_compile_observation_summary` under
    compiler observations. This is the full effect-feed/lowering ledger;
    `uk_compile_rejection_summary` is blocking-only, with missing `blocking`
    treated as blocking for legacy safety.
    Manual-frontier diagnostics record `suggested_claim_template_status` for
    actionable rows, and observations in this summary aggregate those values as
    `suggested_claim_template_status_counts`; human-readable evidence output
    prints the aggregate as `manual compile claim templates: ...`.
  - `uk-replay` JSON and text summaries must also expose blocking compile
    rejection counts/rules separately from total feed-parse/lowering/authority
    rejections. Blocking controls replay-candidate status; total unsupported
    evidence is only the broader ledger.
  - `uk-replay` payload compatibility `compile_rejection_*` fields are
    blocking-only; `compile_observation_*` and `compile_observations` carry the
    full feed-parse/lowering/authority ledger. Rows without an explicit
    `blocking` key are treated as blocking for legacy safety; explicit
    `blocking=false` remains an observation.
  - blocking compile rejection evidence must remain lane-separated
    (`effect_feed_parse`, `lowering`, `authority`) in JSON and text output; a
    total blocking count alone hides which compiler phase blocked replay.
  - `uk-replay` must also expose total compile observations and observation
    rule/lane counts beside the compatibility `compile_rejection_*` fields.
    Nonblocking source observations are part of the ledger even when they do not
    block replay.
  - Human-readable evidence bundle output must also print the UK compiler
    observation lanes: authority summary, compile rejection counts/rules,
    witness-migration counters, oracle-alignment adapter summary, and
    applicability counters. JSON-only visibility is not enough for copied
    proof notes.
  - oracle alignment reports include before/after node counts and
    `node_count_mismatch`; EID grounding is supposed to be an adapter over
    identity labels, not a structure-changing replay phase
  - raw `UKEffectRecord.to_dict()` output used in acquisition manifests must
    expose `requires_applied`, `metadata_only`, `replay_applicable`, `structural`,
    and `structural_for_replay`; manifests are evidence surfaces, not just fetch
    convenience records
  - raw effect serialization must use the model's resolved effective date, not
    the first raw `InForce` entry, because UK effects can carry a prospective
    blank entry before the real commencement date; the raw `in_force_dates`
    witness list should remain visible alongside the resolved date
  - human-readable `uk-effect` output must print enacted/oracle source SHA-256
    identities alongside source status and byte size. JSON-only source identity
    is insufficient for copied diagnostic notes.
  - when effect inspection is truncated and no residual candidate is found in
    the inspected prefix, residual-only output must keep the row visible under
    `uk_effect_inspection_budget_truncated` instead of treating it as clean or
    dropping it
  - `uk-candidates --residual-budget N` separately bounds expensive replay/oracle
    residual analysis; skipped rows must expose `residual_analysis_skipped` /
    `rows_with_residual_analysis_skipped` rather than carrying residual-overlap
    claims
  - if residual analysis cannot run because enacted or oracle source surfaces
    are unavailable, `uk-candidates` must classify the row as
    `residual comparison source unavailable`, emit
    `uk_residual_analysis_source_unavailable`, and keep the row visible under
    `--residual-only`; empty residual sets are not evidence of a clean replay
    when the comparison source is missing
  - this source-unavailable residual-only rule applies even when the inspected
    effect prefix has no candidate effects. Missing comparison source is an
    acquisition fact, not proof that no residual frontier exists.
  - `uk-candidates` human-readable row output must preserve saved benchmark
    rejection rule IDs as a distinct `saved_bench_rejection_rules` lane when
    the saved row carries source-parse, effect-feed, authority, lowering, or
    blocking-lowering rule counts. Full candidate analysis may add live
    residual-compile evidence, but it must not hide the benchmark regime's
    already-observed source/evidence failures.
- residual-backed candidate overlap is branch-symmetric
  - candidate target `section-3` backs residual `section-3-1`
  - candidate target `section-3-1` also backs residual `section-3`
  - sibling branches such as `section-3-1` vs `section-30` remain unrelated
  - same-root siblings such as candidate `section-3-2` and residual `section-3-1`
    do not count as backed residuals just because they share root `section-3`

Current body-crossheading compare invariant:

- replay-only descendants under an oracle `crossheading-*` root can be compare-only
  collapsed subtree shape, just like replay-only descendants under a collapsed
  `section-*` root
  - current example: `ukpga/2006/12` `crossheading-transport-*`
  - normalizing this is compare hygiene, not replay semantics

Current cease-to-have-effect invariant:

- applied `ceases to have effect` rows can be real whole-node repeals even when the
  effects feed marks them non-structural
  - current example: `ukpga/2006/12` `Sch. 4`
  - once compiled as `repeal` and admitted through the narrow nonstructural gate,
    the whole `schedule-4*` replay tail disappears

Current nested-body insertion invariant:

- single-segment non-schedule inserts must prefer the actual parent of the nearest
  existing same-kind predecessor in the body tree, not default blindly to
  `body.children`
  - current example: `ukpga/2006/12` inserted `ss. 15A, 16A, 16B`
  - the enacted/current structure keeps those sections under
    `crossheading -> p1group`, alongside existing `ss. 15/16`
  - inserting them at body top-level created a large fake replay-only tail even
    though the compiled ops themselves were correct
  - once the executor reuses the predecessor parent, `ukpga/2006/12` moves from
    `87.2%` to `99.8%` and the `15A/16A/16B` family leaves the replay frontier

Current mixed-depth sibling-suffix invariant:

- metadata-only sibling suffix expansion must prefer the shallowest valid fixed
  prefix, but only when the remaining sibling family is internally homogeneous
  - current example: `ukpga/2003/30` `s. 1(1A)(a)(b)(c)` means sibling paragraphs
    `a`, `b`, `c` under subsection `1A`
  - counterexample: `s. 10(3)(a)(vi)(vii)` must keep `a` fixed and expand only
    `vi/vii`, not treat `a/vi/vii` as one alpha family
  - a workable rule is:
    - all-numeric groups are siblings
    - all-alpha groups are siblings only when their label length class matches
    - then prefer the shallowest fixed prefix that leaves such a sibling family

Current residual-claim defeat invariant:

- UK frontier triage should distinguish:
  - residual branches that are still backed by candidate effect rows
  - residual branches with no candidate-effect overlap at all
- the latter are defeated frontier claims, not honest replay heads
  - current example: `ukpga/2003/30`
  - after fixing the local `s. 1(1A)(a)(b)(c)` compile bug, the remaining
    residual is dominated by missing `section-3*` / `section-4*` oracle branches
  - the effects inventory contains no `s. 3` or `s. 4` row explaining that
    absence
  - so the residual branch should be treated as unbacked/defeated until a typed
    compare rule or a real backing row appears

## 4. Current `ukpga/2000/41` Frontier Shape

As of the latest `2000/41` pass:

- real replay/compiler fixes removed:
  - malformed schedule-local top-level schedule insertion nesting
  - sibling-suffix target expansion errors
  - deep payload selection misses
  - schedule-local wrapper grounding collisions
  - deep roman descendant fallback drift
- the surviving raw replay-only head is now mainly:
  - `schedule-21-paragraph-7*`
  - `schedule-3`
  - `schedule-3-part-2`

Interpretation:

- `schedule-21` looks like a local oracle omission branch, not a row-local replay defect
  - oracle exposes `schedule-21-paragraph-9*`
  - oracle does not expose the preceding `crossheading` plus `paragraph-7*` / `paragraph-8` block
- `schedule-3` looks closer to whole-branch oracle omission than replay corruption
  - replay retains a coherent surviving `schedule-3` branch
  - oracle exposes no `schedule-3*` EIDs at all
- `ukpga/2000/44` is now a mixed statute rather than a broken pipeline case
  - replay entry-point consistency fixed a false `0.0%` wipeout caused by a
    source-bad whole-act repeal row
  - preserving retained repeal subtrees then moved the statute to a nearly
    solved state (`98.0%`, one oracle-only EID left)
  - `uk-candidates` now distinguishes candidate rows from residual-driving
    candidate rows, so statutes like `2000/44` can become
    `candidate-clean after residual overlap` instead of staying artificially hot

These should be treated as compare-side candidates for future typed normalization or adjudication, not as evidence that the executor is still mutating the wrong provision.

## 5. Immediate UK Next-Step Rule

When choosing the next UK task:

1. prefer rows or statutes still marked `candidate: yes`
2. prefer families with coherent extracted source and a visible live target
3. only patch replay semantics when a deterministic synthetic regression can reproduce the failure
4. otherwise add a typed source/compare classification or living-note finding instead of widening replay fallback behavior

5. for single-segment body inserts, check whether the live predecessor already
   identifies the correct structural parent before inventing new compare classes

## 6. Current Source-Defeat / Metadata-Repeal Invariants

Current source-defeat invariant:

- typed source-pathology is not only reporting; some rows must be prevented from
  mutating replay at all
  - current example: `ukpga/2001/11` `uksi/2001/4022 reg. 20`
  - `uk-effect` correctly classifies it as
    `instruction_text_reused_as_payload`
  - instruction text includes source rows whose supposed payload says that
    words or provisions `become` another unit; unless lowering owns a typed
    renumber/migration operation, that wording is not replacement payload
    text
  - current additional witness: `asp/2003/13` / `asp/2015/9 s. 32(2)(a)`,
    where `the words from "a person" to the end become sub-paragraph (i)` was
    previously reused as paragraph payload and reached replay as a target-leaf
    mismatch
  - before the pipeline change, it still executed as whole-section `replace`
    ops on `ss. 7, 8, 9`, wiping real subtree structure and making
    `2001/11` look like a deep replay failure
  - the replay pipeline should skip structural `insert`/`replace` rows in this
    narrow class rather than executing them and hoping compare-side tooling
    sorts out the damage later

Current metadata-only repeal-series invariant:

- source-bad extracted text does not mean the replay target series is unknowable
  when the affected-provisions metadata already gives a coherent sibling family
  - current examples from `ukpga/2001/11`:
    - `s. 7(3)(4)(4B)(5)` means sibling subsection repeals `3`, `4`, `4B`, `5`
    - `s. 9(1)(a)(b)(bc)(c)(d)` means sibling paragraph repeals
      `a`, `b`, `bc`, `c`, `d`
  - these should compile from metadata even when the extracted source is the
    broad schedule part and is therefore typed
    `instruction_text_reused_as_payload`
  - a workable rule is:
    - all `\d+[A-Z]*` sibling groups can be treated as one subsection family
    - all one/two-letter alpha groups can be treated as one paragraph family
    - do not broaden beyond that without a fresh deterministic regression
- metadata effect types beginning `repealed by ...` are also structural repeals
  when the affected-provisions metadata names a concrete target. The cited
  repealing instrument is provenance for the repeal, not payload text needed to
  execute the deletion.
  - current witness: `asp/2000/6` / `uksi/2010/2279 Sch. 2`, where
    `Sch. 2 para. 2` previously stayed in replay because `repealed by 2010
    c. 15 Sch. 27 Pt. 1 (as substituted)` was treated as nonstructural

Current `ukpga/2001/11` interpretation:

- after applying the two rules above, `ukpga/2001/11` improved from `63.2%` to
  `95.4%`
- the earlier huge `section-7*` / `section-9*` tail was therefore mostly not a
  deep executor problem
- the remaining tail is now small enough that it should be diagnosed family by
  family, not treated as evidence of another broad replay failure mode

Current lead-in sibling amendment invariant:

- some affecting-act provisions are only amendment lead-ins, and the real
  inserted/replaced content lives in the immediately following sibling
  `BlockAmendment`
  - current example: `ukpga/2001/11` affected `s. 9(7)-(9)` via
    `ukpga/2009/24 Sch. 4 para. 4(3)`
  - the matched provision node is:
    - `P2 id="schedule-4-paragraph-4-3"` with text
      `After subsection (6) insert—`
  - the actual inserted subsections `7`, `8`, `9` are in the next sibling
    `BlockAmendment`
- extraction should therefore prefer the following sibling amendment block when:
  - the ID-matched node is a lead-in ending in `insert—` or `substitute—`
  - and the next structural sibling is `BlockAmendment` / `InlineAmendment`
- after applying this rule, `ukpga/2001/11` improved again from `95.4%` to
  `98.1%`, leaving only:
  - replay-only `section-10-3-bc`
  - oracle-only `section-10-3-cn1`
  - oracle-only `section-7-9-b-i/ii/iii`
  - oracle-only `section-8-2-b`

Current block-substitution context invariant:

- a matched provision that contains both explicit substitution wording and a
  descendant `BlockAmendment` owns the instruction context; extraction must not
  return the naked `BlockAmendment` payload alone
  - current example: `asc/2023/1` affected `s. 25(2)` via
    `wsi/2024/782 reg. 46(2)`
  - the source provision says `for "with" to the end substitute-` and the
    block carries the replacement text
  - returning only the block loses the action and anchor, producing
    `fragment_context_missing` / `uk_effect_overlap_substitution_unlowered`
  - preserving the exact `P2 id="regulation-46-2"` provision lets lowering
    emit a bounded `text_replace` at `section:25/subsection:2`
- secondary legislation references like `reg.` / `regs.` must preserve
  `regulation` identity during affecting-source extraction; normalizing them to
  `section` is a target-kind mutation, not a harmless parser shortcut

Current parser-surface normalization invariant:

- UK source text is not mutated for storage, replay payloads, or source
  witnesses just to make parser regular expressions easier
- `normalize_uk_parser_text(...)` builds a parser-only view:
  - transport whitespace is collapsed, matching the existing parser behavior
  - dash-like instruction punctuation outside quoted legal text is normalized
    to a single dash surface
  - quoted legal preimages and payloads are preserved after whitespace collapse
- this normalizer belongs before source-language parsing, not in replay apply or
  oracle comparison
- if a normalization changes target resolution, legal structure, quoted legal
  text, or replay matching, it must become a typed observation/finding rather
  than a silent parser convenience

Current payload-descendant source-ref invariant:

- effects metadata may cite a source instruction that is absent from the
  affecting XML while a same-labelled amended-text payload child exists inside
  a descendant `BlockAmendment`
  - current example: `ukpga/2020/17` affected `s. 343(2)` via
    `ukpga/2022/32 s. 175(2)(b)`
  - the XML exposes `section-175-2` and `section-175-2-a`; there is no direct
    source instruction `section-175-2-b`
  - greedy extraction previously selected an anonymous payload `P3(b)` inside
    the replacement `BlockAmendment`
- this is target/source hijacking: the payload child is amended text, not the
  cited source instruction
- LawVM rejects that extraction with
  `uk_affecting_act_block_amendment_payload_descendant_ref_rejected`
  - family: `source_pathology`
  - phase: `extraction`
  - strict disposition: `block`
  - quirks disposition: `record`
- the rule is narrow: direct anonymous instruction children outside
  `BlockAmendment` / `InlineAmendment` remain extractable
- the parser may lower `for "X" to the end substitute- <block text>` to a
  `TEXT_FROM_X_TO_END` patch using the named rule
  `uk_effect_quoted_anchor_to_end_block_substitution_text_patch`
- the parser also accepts `for the words "X" to the end substitute "Y"` as the
  same bounded range-to-end selector, with rule
  `uk_effect_quoted_words_anchor_to_end_substitution_text_patch`. This covers
  quoted replacement text and does not authorize target-subtree flattening
  beyond the existing `TEXT_FROM_X_TO_END` replay rules. Current witnesses:
  `ukpga/1962/46` affected `s. 65(5)` by `uksi/2014/560 Sch. 1 para. 9` and
  `uksi/2014/3229 Sch. 5 para. 4`.
- parser-owned post-child local text tails are preserved as source-shape
  evidence, not inferred at replay time. When UK XML stores paragraph children
  between an introductory `Text` node and a trailing local `Text` node, the
  grafter records `uk_post_child_text_tail` on the affected IR node and emits
  `uk_post_child_text_tail_preserved`. A range-to-end substitution whose start
  anchor is found inside that marked tail may be replayed node-locally under
  `uk_replay_node_local_range_to_end_text_rewrite_applied`, preserving the
  existing child paragraphs. Unmarked `TEXT_FROM_X_TO_END` over a node with
  descendants remains the explicit subtree-flattening lane
  `uk_replay_subtree_range_to_end_text_rewrite_flattened`; replay must not
  guess that arbitrary parent text after children is legally local tail text.
  Current witness: `ukpga/2020/15` affected `s. 1(6)` and `s. 1A(5)` by
  `ukpga/2025/8 s. 52(2)`.
- manual-frontier classification treats explicit inflected amendment verbs
  (`substituted`, `inserted`, `omitted`, `repealed`) as deterministic
  parser/extraction work for `uk_effect_overlap_substitution_unlowered`, not as
  an unclassified frontier. Witness pattern: `For the period ... there is
  substituted the period of four years.`
- preposed passive word substitutions of the form `there shall be substituted
  for the words "X" the words "Y"` are deterministic text patches, not manual
  overlap frontier rows. Lowering emits
  `uk_effect_preposed_passive_substitution_text_patch` and must preserve the
  explicit effect target. Current witness: `ukpga/1990/16` affected `s. 9(5)`
  by `uksi/2004/3279 reg. 11(b)`.
- passive same-target text substitutions of the form `for "X", wherever
  occurring, there shall be substituted "Y"` are also deterministic text
  patches under `uk_effect_wherever_occurring_substitution_text_patch`. When
  the same formula names multiple quoted preimages joined by `and`, the parser
  emits one all-occurrences patch per quoted preimage and the same replacement;
  it does not infer any additional preimages from the target text. The related
  passive range form `for the words from the beginning to "X" there shall be
  substituted "Y"` lowers to `TEXT_FROM__TO_X` using
  `uk_effect_from_beginning_passive_substitution_text_patch`. Passive bounded
  range forms such as `for the words from "X" to "Y" there shall be
  substituted "Z"`, `for the words from "X" to the end of paragraph (c) there
  shall be substituted "Z"`, and `for the words from "X" onwards there shall
  be substituted "Z"` lower to the same explicit range selectors used by their
  active `substitute` counterparts. Current witness: `ukpga/1990/42` affected
  `s. 52`, `s. 14`, `s. 104(6)`, `s. 97(1)`, `s. 88(7)`, and `s. 53(8)` by
  `ukpga/2003/21 Sch. 15`.
- preposed beginning insertions of the form `there shall be inserted at the
  beginning the words "X"` lower to an explicit `TEXT_BEGINNING` text patch
  under `uk_effect_preposed_beginning_text_insertion_patch`; this is still a
  target-local text operation and does not authorize rewriting the host
  subsection/paragraph. Current witness: `ukpga/1990/16` affected
  `s. 40(4)(a)` by `uksi/2004/2990 reg. 4(a)`.
- passive quoted omissions of the form `the word/words "X" shall be omitted`
  lower to target-local text deletion under
  `uk_effect_quoted_word_passive_omit_text_patch`. This rule requires an
  explicit quoted preimage and does not apply to `the entry for "X" shall be
  omitted`, which remains a table/entry-boundary claim. Current witness:
  `ukpga/1990/42` affected `s. 185(5)` by `ukpga/2003/21 Sch. 15
  para. 64(1)(b)`; `ukpga/1990/16` affected `s. 9(1)` by `uksi/2004/3279
  reg. 11(a)(i)`.
- passive range omissions of the form `the words from "X" onwards shall be
  omitted` lower to `TEXT_FROM_X_TO_END` under
  `uk_effect_range_to_end_passive_repeal_text_patch`. This is a bounded text
  deletion from the explicit start preimage to the end of the target text
  surface; it does not authorize deleting a parent or sibling. Current witness:
  `ukpga/1990/42` affected `Sch. 6 para. 13(2)` by `ukpga/2003/21 s. 342(a)
  Sch. 19(1)`.
- passive from-beginning substitutions of the form `for the words from the
  beginning of the subsection to "X" are substituted the words "Y"` lower to a
  bounded `TEXT_FROM__TO_X` target-local text replacement under
  `uk_effect_from_beginning_passive_substitution_text_patch`. The optional
  `of the subsection/paragraph/sub-paragraph/section` phrase is selector
  context, not permission to retarget outside the explicit affected provision.
  Current witness: `ukpga/1991/22` affected `s. 10(1)` by `uksi/2003/1398
  Sch. para. 18(2)(a)`.
- passive range-to-end repeals of the form `the words from "X", where thirdly
  occurring, to the end are repealed` lower to `TEXT_FROM_X_TO_END` deletion
  under `uk_effect_range_to_end_passive_ordinal_repeal_text_patch` when the
  ordinal is explicit. The non-ordinal variant is
  `uk_effect_range_to_end_passive_repeal_text_patch`. This is a text-range
  selector inside the cited affected target, not a structural repeal of the
  host provision. Current witness: `ukpga/1991/22` affected `s. 114(1)` by
  `asp/2005/12 s. 19(4)(a)`.
- parenthesized text-anchor insertions of the form `after (3) insert "X"` lower
  to replacement of the literal text anchor `(3)` under
  `uk_effect_after_parenthesized_anchor_insert_text_patch`. The rule is
  deliberately text-local and requires an explicit affected target plus a
  quoted insertion payload; it does not interpret `(3)` as a structural child
  target. Current witness: `ukpga/1991/22` affected `s. 48(5)` by
  `ukpga/2025/34 s. 49(5)(b)`.
- bare range substitutions of the form `from "X" to "Y" substitute <unquoted
  payload>` lower to `TEXT_FROM_X_TO_Y` target-local text replacement under
  `uk_effect_bare_range_unquoted_substitution_text_patch`. This is the same
  selector family as `for the words from "X" to "Y" substitute ...`, but covers
  the source surface that omits `for the words`. The rule requires explicit
  quoted start and end anchors plus an explicit affected target; if the source
  payload needs independent structural ownership, it should be split earlier
  rather than widened during replay. Current witness: `ukpga/1991/22` affected
  `s. 48(5)` by `ukpga/2025/34 s. 49(5)(a)`.
- end-position word insertions of the form `insert "X" at the end of
  sub-paragraph (Y)` lower to a target-local append under
  `uk_effect_insert_text_at_end_patch` when the effect target already names the
  receiving provision. The source phrase does not authorize rebinding to a
  sibling child; mismatched effect targets remain unresolved. Current witness:
  `ukpga/1992/8` affected `s. 103(4)(a)(ii)` by `uksi/2011/1484 Sch. 7
  para. 15(a)(i)`.
- Passive end-position insertions of the form `the word "X" shall be inserted
  at the end of paragraph (Y)` lower to the same target-local append selector
  under `uk_effect_passive_insert_text_at_end_patch`, again only because the
  effect-feed target supplies the receiving paragraph. Current witness:
  `ukpga/1990/42` affected `s. 104B(1)(a)` by `ukpga/2003/21 s. 255(a)`.
- definition-scoped after-anchor insertions under
  `uk_effect_in_definition_after_anchor_insert_text_patch` may carry nested
  quoted terms inside the inserted payload. The parser must bind the payload to
  the final closing quote of the instruction rather than truncating at the
  first nested quote, but the selector remains the explicit definition term plus
  explicit quoted anchor. The same family accepts an optional comma between the
  anchor and `insert`. Current witnesses: `ukpga/1992/8` affected `s. 115B(9)`
  by `uksi/2014/1283 Sch. para. 4(c)(ii)`, and `ukpga/1962/46` affected
  `s. 52(4)` by `uksi/2012/1659 Sch. 2 para. 21`.
- compound child-anchor insertions of the form
  `after subsection (4)(a)(i), insert "X"` lower under
  `uk_effect_after_compound_subsection_child_text_insertion_patch` to a
  target-local child selector such as `TEXT_AFTER_CHILD_subparagraph_i`.
  Replay may only append to that child when the anchor is unique inside the
  already affected target; absent or ambiguous anchors remain blocking replay
  gaps. Current witness: `ukpga/1992/8` affected `s. 103(4)(a)` by
  `uksi/2019/479 reg. 67(a)`.
- source-row labelled sibling insertions of the form
  `b after paragraph (aa) insert- ab ...` lower under
  `uk_effect_after_paragraph_insert_single_label_lowered` only when the effect
  target names the inserted sibling, the source row label is stripped as source
  table/list presentation, and the source anchor's next alphabetical label is
  the same inserted label. This is source-context elaboration, not payload
  smuggling: the instruction prose is not retained as the payload text, and the
  observation records the source row label, anchor, inserted target, and
  source instruction. Current witness: `ukpga/1992/8` affected
  `s. 103(4)(ab)` by `nisr/2012/413 Sch. 4 para. 3(b)`.
- grouped parent after-anchor insertions of the form `after-- <child rows>
  insert "X"` may lower a child row under
  `uk_effect_source_parent_grouped_after_anchor_insert_text_patch` only when
  the child row supplies a single quoted anchor and the parent tail supplies the
  insertion payload. Rows that state `in both places` or equivalent lower under
  `uk_effect_source_parent_grouped_after_anchor_all_occurrences_insert_text_patch`
  and rely on the explicit text-selector contract that `occurrence=0` replaces
  all matches inside the affected target. Current witnesses: `ukpga/1992/8`
  affected `s. 138(1)` by `ukpga/2005/6 Sch. 1 para. 51(a)` and para. 51(b).
- final bare quoted word repeals of the form `the "and" at the end of
  paragraph (aa) is repealed` lower to deletion of the final occurrence of the
  quoted token under `uk_effect_final_bare_quoted_word_repeal_text_patch`.
  This rule requires an explicit quoted preimage and does not delete structural
  children. Current witness: `ukpga/1992/8` affected `s. 103(4)(aa)` by
  `nisr/2012/413 Sch. 4 para. 3(a)`.
- the parser may lower after-anchor insertions with adverbial ordinal wording
  such as `after "board", where secondly occurring, there is inserted "..."` to
  `uk_effect_after_quoted_anchor_where_ordinal_insert_text_patch`, preserving
  `occurrence=2`. This is only allowed for explicit quoted anchors and quoted
  insertion payloads. Current witness: `asp/2003/1` affected `s. 61(c)(iv)` by
  `asp/2005/12 Sch. 1 para. 15(5)`.
- definition-scoped range-to-end substitutions may also preserve explicit
  source occurrence, e.g. `in the definition of "joint fire board" for the
  words from "board", where it secondly occurs, to the end substitute "..."`.
  Lowering emits
  `uk_effect_definition_range_to_end_occurrence_substitution_text_patch`, and
  replay uses the requested occurrence within the definition entry instead of
  requiring the start anchor to be unique. Current witness: `asp/2003/1`
  affected `s. 61` by `asp/2005/5 Sch. 3 para. 23(5)`.
- the parser may lower `after the definition of "X" insert- <block text>` and
  `after the definition of "X", insert- <block text>` to a
  `TEXT_AFTER_DEFINITION_X` patch using the named rule
  `uk_effect_after_definition_text_insertion_patch`; replay applies it only
  when the target text has a semicolon-terminated definition entry for `X`.
  Replay may recognize a parenthetical translation between the quoted defined
  term and predicate, for example `"2013 Act" ("Deddf 2013") means ...`, under
  `uk_replay_definition_anchor_parenthetical_translation_normalized`; this is a
  recorded target-resolution recovery, not permission to insert after a
  different definition term
- the parser may lower `after the definitions of "X" and "Y" insert- <block
  text>` to a `TEXT_AFTER_DEFINITION_Y` patch using the named rule
  `uk_effect_after_definitions_text_insertion_patch`; the anchor is the final
  quoted definition because the source inserts after the listed definition
  group. Replay may recognize the final anchor term inside a shared definition
  entry such as `"directed" and "intrusive", in relation to surveillance, shall
  be construed ...`; successful application records
  `uk_replay_definition_anchor_conjoined_term_normalized` and, where present,
  `uk_replay_definition_anchor_qualifier_phrase_normalized`. These are bounded
  target-resolution recoveries: the quoted anchor term and definition predicate
  must both be present. Current witness: `asp/2000/11` affected `s. 31(1)` by
  `asp/2010/13` `s. 106(8)`, and later by `ukpga/2016/25`
  `Sch. 10 para. 94(2)`.
- the parser may lower `at the beginning of subsection (N) insert "X"` to a
  `TEXT_BEGINNING` patch using the named rule
  `uk_effect_beginning_text_insertion_patch`; the feed already supplies the
  affected subsection, so the phrase is target confirmation, not a reason to
  widen scope. Current witness: `asp/2001/2` affected `s. 49(1)` by
  `asp/2008/1` `sch. 1 para. 2(a)`.
- the same beginning-insertion family accepts older `there shall be inserted`
  wording, including an optional `the word(s)` carrier. It still lowers to
  `TEXT_BEGINNING` only against the effect-feed target; it must not use the
  carried subsection/paragraph words to widen scope. Current witness:
  `asp/2000/5` affected `s. 18(6)` by `asp/2003/9`
  `Sch. 13 para. 3(b)`.
- the parser may lower `after "X" there shall be inserted "Y"` and `after the
  word "X" there shall be inserted the words "Y"` through
  `uk_effect_after_quoted_anchor_insert_text_patch`. This is an explicit
  quoted-anchor text rewrite, not a structural child insertion. Current
  witnesses: `asp/2000/4` affected `s. 24(4)` by `asp/2006/2 s. 36(b)` and
  `asp/2000/5` affected `s. 25` by `asp/2003/9 Sch. 13 para. 5(b)`.
- the parser may lower older bare quoted-anchor insertion shorthand such as
  `"18," there shall be inserted "18A, 18B, 18C,"` through
  `uk_effect_bare_quoted_anchor_insert_text_patch`. This is distinct from the
  explicit `after "X"` family: it is only accepted when the source row consists
  of an optional `the word(s)` carrier, a quoted anchor, `there
  is/are/shall be inserted`, a quoted insertion, and only terminal comma,
  semicolon, or full stop punctuation; the effect metadata must already supply
  the target provision.
  Current witnesses include `asp/2000/5` affected `s. 43(2)(a)` by
  `asp/2003/9 Sch. 13 para. 8(a)(i)` and affected `s. 17(1)` by
  `asp/2003/9 Sch. 13 para. 2(a)(i)`, plus affected `s. 43(3)(a)` by
  `asp/2003/9 Sch. 13 para. 8(c)(ii)`.
- the parser may lower `after "X" where it first/second occurs insert "Y"`
  and the same comma-separated passive form `after "X", where it second
  occurs, there is inserted "Y"` to an occurrence-qualified anchor patch using
  the named rule
  `uk_effect_after_quoted_anchor_where_ordinal_insert_text_patch`. Current
  witnesses: `asp/2001/2` affected `s. 79(2)` by `asp/2019/17`
  `sch. para. 3(7)(b)`, and `asp/2002/11` affected `s. 5(1)(a)` by
  `asp/2010/11 Sch. 3 para. 1`.
- the parser may lower `after "X", in the second place where it occurs, insert
  "Y"` and bounded multi-ordinal forms such as `in the first and second places`
  to occurrence-qualified text patches using
  `uk_effect_after_quoted_anchor_ordinal_places_insert_text_patch`. Multi-place
  insertions emit one patch per ordinal in descending occurrence order so replay
  mutates later occurrences before earlier ones. This rule requires an explicit
  quoted anchor, explicit ordinal place wording, and a quoted insertion payload;
  older passive wording such as `there shall be inserted "Y"` is accepted in
  the same family. The rule does not recover source-carried block fragments
  with missing parent context. Current witnesses: `ukpga/1985/66` affected
  `s. 56C(1)` by `asp/2014/11 s. 33(3)` and affected `s. 17(8)` by
  `asp/2014/11 s. 26(3)(d)`, plus `ukpga/1970/9` affected `s. 37A` by
  `ukpga/1994/9 Sch. 8 para. 13(a)`.
- the parser may lower quoted-word substitutions with explicit multi-ordinal
  occurrence wording such as `for the word "X", where it first and third
  occurs, substitute "Y"` to one occurrence-qualified text patch per ordinal
  using `uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch`.
  Patches are emitted in descending occurrence order to avoid earlier edits
  shifting later matches. This requires an explicit quoted preimage, quoted
  replacement, and source-owned ordinal occurrence phrase. Current witnesses:
  `ukpga/1985/66` affected `s. 2(1)` by `asp/2007/3 Sch. 1 para. 3(3)`,
  affected `s. 2(2)` by `asp/2007/3 Sch. 1 para. 3(4)(b)`, and affected
  `s. 24(2)` by `asp/2007/3 Sch. 1 para. 23(2)`.
- Some source rows omit the insertion verb while the official effect feed
  supplies `words inserted`, for example `after "hospital" where firstly
  occurring "or in a care home service"`. These may lower under
  `uk_effect_metadata_carried_after_ordinal_insert_text_patch` only when the
  feed action is insertion and the source row supplies a quoted anchor, explicit
  ordinal occurrence, and quoted payload. This is an effect-feed elaboration
  observation, not a generic parser action. Current witnesses: `asp/2003/13`
  affected `s. 254(7)(b)` and `s. 254(8)(b)` by `ssi/2005/465
  Sch. 1 para. 32(21)`.
- the parser may lower inverted wording `the word "Y" is inserted after the
  word "X" where it second appears` to the same text-patch shape, using
  `uk_effect_word_inserted_after_word_where_ordinal_text_patch`. The inserted
  word is not treated as a structural payload; the quoted preimage and ordinal
  remain source-owned. Current witness: `asp/2000/1` affected `Sch. 2 para. 2`
  by `asp/2010/8 s. 118(8)(a)(i)`.
- the parser may lower imperative `repeal the words "X"` to a text-removal
  patch using the named rule `uk_effect_repeal_quoted_words_text_patch`.
  Current witness: `asp/2000/11` affected `s. 24(2)(b)` by `asp/2012/8`
  `sch. 7 para. 15(12)(c)`.
- the parser may lower `the word "X" at the end of paragraph (...) is
  repealed` to a final-occurrence text deletion using
  `uk_effect_final_quoted_word_repeal_text_patch`. It records occurrence `-1`
  rather than deleting every occurrence of the word in the target. Current
  witness: `asp/2000/4` affected `s. 16(6)(a)` by `asp/2006/4 s. 57(2)(a)`.
- the parser may lower `for the words from "X", where second occurring, to
  "Y" substitute "Z"` to an occurrence-qualified `TEXT_FROM_X_TO_Y` patch using
  `uk_effect_range_occurrence_substitution_text_patch`. Current witness:
  `asp/2000/11` affected `s. 16(1)(a)` by `asp/2012/8`
  `sch. 7 para. 15(8)(b)`. Replay treats a quoted single-word range anchor as
  a word token for ordinal counting, not as an arbitrary substring; otherwise
  `"an", where second occurring` can be hijacked by the leading letters of
  `any`. Successful application emits nonblocking
  `uk_replay_text_range_anchor_word_boundary_normalized` with
  `family=text_match_recovery` and `strict_disposition=record`.
- the parser may lower `for the words from "X", where it first occurs, to "X",
  where it second occurs, substitute "Z"` to a bounded same-anchor range patch
  using
  `uk_effect_same_anchor_adjacent_occurrence_range_substitution_text_patch`.
  This is deliberately limited to the same quoted anchor and adjacent
  occurrence numbers. Current witness: `asp/2000/6` affected `s. 6(1)(a)` by
  `asp/2016/8` `s. 3(4)(a)(ii)`.
- the parser may lower `for the words from "X", where first occurring, to "Y",
  where second occurring, substitute "Z"` to a `TEXT_FROM_X_TO_Y` patch whose
  typed selector records both start `occurrence` and independent
  `end_occurrence`. Lowering emits
  `uk_effect_range_independent_end_occurrence_text_patch` as a nonblocking
  text-rewrite observation. Replay must use the named end-anchor ordinal
  rather than silently selecting the first `Y` after `X`; if the named end
  occurrence is absent or precedes the start anchor, the text patch fails with
  ordinary preimage diagnostics. Current witness: `asp/2000/4` affected
  `s. 55` by `asp/2007/10` `s. 59(2)`.
- source-context elaboration may lower `after the words inserted by
  sub-paragraph/paragraph (A) insert "Y"` or `insert- Y` only when the cited
  sibling source provision is present under the same source parent and parses
  to exactly one deterministic text fragment. The anchor is the cited sibling's
  replacement text; lowering emits
  `uk_effect_after_words_inserted_by_sibling_text_patch` as a nonblocking
  `source_context_elaboration` observation. If the sibling is absent,
  ambiguous, or unparseable, the row remains blocked. Current witnesses:
  `asp/2000/11` affected `s. 24(2)(b)` by `asp/2012/8`
  `sch. 7 para. 15(12)(b)` and affected `s. 11(4)(a)` by `sch. 7 para.
  15(5)(d)(i)(B)`.
- the parser may lower `for the words from "X" to "Y" substitute Z` where `Z`
  is unquoted block text to `TEXT_FROM_X_TO_Y` using
  `uk_effect_range_unquoted_substitution_text_patch`. Current witness:
  `asp/2000/11` affected `s. 14(5)(a)` by `asp/2012/8`
  `sch. 7 para. 15(7)`.
- the parser may lower `omit the words "X"` to a text-removal patch using
  `uk_effect_direct_quoted_word_omission_text_patch`. Current witness:
  `asp/2000/1` affected `s. 13(5)(c)` by `uksi/2007/825` `reg. 4(2)(b)`.
- the parser may lower `immediately before the word "X" insert "Y"` to a
  before-anchor text patch using
  `uk_effect_immediately_before_word_insert_text_patch`; if the source says
  `where it occurs for the second time`, the patch carries occurrence `2` and
  uses `uk_effect_immediately_before_word_ordinal_insert_text_patch`. Current
  witnesses: `asp/2000/1` affected `s. 12(2)(a)` and `sch. 3 para. 1` by
  `asp/2010/8` `s. 118(3)` and `s. 118(9)(a)`.
- the parser may lower `after "X" insert- Y` where `Y` is unquoted block text
  to an after-anchor text patch using
  `uk_effect_after_quoted_anchor_block_insert_text_patch`. Current witness:
  `ukpga/2022/32` affected `Sch. 3 Pt. 1` by `uksi/2023/575` `reg. 2(2)`.
- the parser may lower `for "X" substitute- Y` where `Y` is unquoted block text
  to a quoted-anchor text patch using
  `uk_effect_quoted_anchor_block_substitution_text_patch`. Current witness:
  `ukpga/2022/32` affected `Sch. 3 Pt. 3` by `uksi/2023/424`
  `Sch. para. 22`.
- the same unquoted block substitution rule also covers `for the words "X"
  substitute Y` when `Y` is unquoted block text and the target is explicit.
  Current witness: `asp/2000/4` affected `s. 47(6)(b)` by `asp/2005/13`
  `s. 35(2)(f)(ii)`.
- the parser may lower `leave out "X" and insert "Y"` to an ordinary
  replacement patch using `uk_effect_leave_out_and_insert_text_patch`. This is
  a text replacement, not a repeal followed by an unrelated insertion. Current
  witness: `asp/2000/4` affected `s. 15(3)(c)` by `asp/2007/10`
  `s. 57(1)(b)(i)`.
- the parser may lower `after "X", where last occurring, insert "Y"` to an
  after-anchor patch with occurrence `-1` using
  `uk_effect_after_quoted_anchor_last_occurrence_insert_text_patch`. Current
  witness: `asp/2000/4` affected `s. 58(6)` by `asp/2007/10`
  `s. 60(2)(a)(ii)`.
- post-quoted ordinal substitutions do not require an explicit pronoun after
  `where`: `for "X", where first occurring, substitute "Y"` lowers through
  `uk_effect_post_quoted_where_ordinal_substitution_text_patch` with occurrence
  `1`. Current witness: `asp/2000/4` affected `s. 64(1)` by `asp/2007/10`
  `s. 60(4)(b)`.
- post-quoted ordinal substitutions may also use passive `there is substituted`
  wording after an explicit occurrence selector, for example `for the word "X"
  in the second place where it appears there is substituted "Y"`. This lowers
  under `uk_effect_post_quoted_ordinal_substitution_text_patch`, preserves the
  bounded occurrence selector, and is not an all-occurrences rewrite. Current
  witness: `asp/2002/3` affected `s. 30(4)` by `asp/2005/3`
  `s. 21(2)(c)(ii)`.
- the same passive ordinal-place family may include the replacement wrapper
  `the words`, for example `for the words "X", in the first place where they
  occur, there shall be substituted the words "Y"`. This remains a bounded
  occurrence text replacement under
  `uk_effect_post_quoted_ordinal_substitution_text_patch`, not a source-shape
  repair. Current witness: `ukpga/1970/9` affected `s. 8A(1)` by
  `ukpga/1995/4` `s. 103(3)(b)`.
- multi-ordinal substitutions may express the occurrence selector as ordinal
  places rather than direct `where it first and third occurs` wording: `for
  "X", in the first and third places where it occurs, substitute "Y"` lowers
  under `uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch`
  and emits one text replacement per selected occurrence in descending order.
  Current witnesses: `ukpga/1985/66` affected `s. 17(1)` and `s. 17(6)` by
  `asp/2007/3` `s. 16(4)(a)(ii)` and `s. 16(4)(d)(ii)`.
- the parser may lower `for the words from "X" where it first appears to the
  end substitute- Y` where `Y` is unquoted block text to `TEXT_FROM_X_TO_END`
  with the recorded start occurrence using
  `uk_effect_range_to_end_ordinal_block_substitution_text_patch`. Current
  witness: `asp/2000/4` affected `s. 58(6)` by `asp/2010/8`
  `sch. 1 para. 11(2)(b)`.
- the parser may lower `for the words from "X" to the end, substitute "- Y`
  where the replacement has an opening quote before a block dash but no closing
  quote in the flattened source text to `TEXT_FROM_X_TO_END` using
  `uk_effect_range_to_end_open_quote_block_substitution_text_patch`. The rule is
  limited to an explicit `from "X" to the end` text span and does not infer
  structural boundaries such as `after paragraph (b)`. Current witness:
  `ukpga/2021/12` affected `Sch. 9 para. 1(3)` by `uksi/2025/1284`
  `sch. 4 para. 3`.
- unquoted `from "X" to the end ... substitute - Y` may include whitespace
  between `substitute` and the dash; this remains the same
  `uk_effect_anchor_to_end_block_substitution_text_patch` family, not a new
  action. Current witness: `asp/2000/4` affected `s. 41(2)(a)(iii)` by
  `asp/2010/8` `sch. 2 para. 5(2)(a)`.
- grouped occurrence substitutions may carry the quoted anchor in a parent
  instruction and the ordinal/replacement in child rows, e.g. parent `for
  "X"-` and child `the first time it appears, substitute "Y"`. Lowering may
  combine those source-local facts into an occurrence-qualified text patch
  using `uk_effect_grouped_anchor_occurrence_substitution_text_patch`, with a
  `source_context_elaboration` observation. Current witnesses: `asp/2000/4`
  affected `Sch. 1 para. 1` by `ssi/2011/211` `Sch. 1 para. 8(4)(a)(i)-(ii)`.
- `after "X", on each occasion where it appears, insert "Y"` is an explicit
  all-occurrences insertion and lowers to the same target-scoped all-occurrences
  text-patch semantics as `in each place it occurs`, using
  `uk_effect_after_quoted_anchor_each_occasion_insert_text_patch`. Current
  witness: `asp/2007/3` affected `s. 218` by `ssi/2019/51` `reg. 6(6)`.
- `after "X", in both places where it appears, insert "Y"` is the same
  source-owned all-occurrences insertion family as `in both places insert`.
  Lowering uses `uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch`
  and records `explicit_all_occurrences_text_patch`; it must remain scoped to
  the effect-feed target. Current witnesses: `asp/2007/3` affected `s. 216(4)`
  by `ssi/2019/51` `reg. 6(4)(b)` and `s. 214(2)` by `reg. 6(2)(b)`.
- Older passive drafting of the same family may say `after the words "X", in
  each place where they occur, there shall be inserted "Y"`. This lowers under
  `uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch`; the phrase
  `where they occur` is part of the all-occurrences signal and must not be
  missed merely because it follows `the words`.
- The same all-occurrences family also accepts compressed wording `after "X",
  in each place occurring, insert "Y"`. This is still an explicit
  all-occurrences text rewrite over the effect-feed target, not a target search
  expansion. Current witnesses: `ukpga/1962/46` affected `s. 27(6)` and
  `s. 28(2)-(4)` by `uksi/2012/1659 Sch. 2 paras. 17(3), 18`.
- `after each occurrence of "X" insert "Y"` also lowers through
  `uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch`. It is a
  quoted-anchor text rewrite over every target occurrence, not a structural
  child insert. Current witness: `asp/2001/11` affected `s. 1(2)(b)` by
  `ssi/2005/623 art. 21(2)(i)`.
- `after "X", where it first occurs, insert "Y"` may contain nested quoted
  terms inside the inserted payload. The parser uses an end-anchored payload
  scan only for that nested-quote case, lowering to
  `uk_effect_after_quoted_anchor_where_ordinal_nested_quote_insert_text_patch`;
  the ordinal target remains explicit and replay applies only that occurrence.
  Current witness: `asp/2007/3` affected `s. 63(1)(a)` by `asp/2010/8`
  `sch. 4 para. 15(1)(a)(i)`.
- `after "X" insert- "<term>" means ...` is a bounded interpretation-section
  definition insertion when the quoted block payload starts with a definition
  predicate such as `means`. It lowers to
  `uk_effect_after_quoted_anchor_definition_entry_block_insert_text_patch` as a
  text patch after the explicit quoted anchor; generic quoted block payloads
  remain unsupported. Current witness: `asp/2007/3` affected `s. 47` by
  `ukpga/2009/1` `s. 253(7)`.
- `before/after the entry relating to "X" insert- "Y"` is not a text-patch
  family. It lowers to `uk_effect_schedule_list_entry_insert` only when the
  affected target is a schedule, explicit schedule partition, or an explicit
  schedule paragraph/subparagraph carrier and source parsing has a typed
  `schedule_entry` carrier. Replay then requires exactly one direct
  schedule-entry anchor on that carrier and inserts a
  `schedule_entry` sibling before/after that anchor. Missing or ambiguous
  anchors block with
  `uk_replay_schedule_list_entry_anchor_unresolved`, classified as source
  shape, not replay bug. If the cited anchor is the unique prefix of a longer
  descriptive entry, replay may proceed with nonblocking
  `uk_replay_schedule_list_entry_anchor_prefix_normalized`; it may not choose
  among multiple prefix matches. If the only mismatch is a leading article
  (`the`/`a`/`an`) on either the source anchor or preserved entry text, replay
  may proceed with nonblocking
  `uk_replay_schedule_list_entry_anchor_article_normalized`. For explicit
  schedule-partition carriers, an anchor of the form `the X (paragraph N)` may
  proceed with nonblocking
  `uk_replay_schedule_list_entry_anchor_parenthetical_paragraph_normalized`
  only when both the paragraph label `N` and the normalized entry text `X`
  resolve to one direct entry in that partition. Source forms such
  as `there is inserted the following entry- ...`, `the insertion, after the
  entry for X, of Y`, and quoted schedule-list anchors like `insert before
  "X"- Y` are part of this family. Explicit `at the appropriate place in
  alphabetical order insert- Y` forms lower to the same typed entry-insert
  family with an alphabetical placement selector; replay records
  `uk_replay_schedule_list_entry_alphabetical_position_resolved` and blocks if
  the inserted entry is empty or already present. The explicit alphabetical
  source form may say either `at the appropriate place` or
  `at an appropriate place`; non-alphabetical `appropriate place` wording
  remains a manual/frontier placement claim. Rows that look like
  schedule-list-entry amendments but
  lack enough carrier/target information remain in
  `schedule_list_entry_target_unsupported` /
  `uk_manual_frontier_schedule_list_entry_candidate`. Current witnesses:
  `asp/2000/7` affected `sch. 3` by `asp/2005/6` `Sch. 3 para. 9(a)`,
  `ssi/2009/286` `art. 2(2)(c)`, `asp/2010/8` `sch. 14 para. 1(b)`,
  `asp/2005/10` `sch. 4 para. 12`, and `asp/2007/5` `Sch. 5 para. 4`.
  If a source-owned payload on such an explicit carrier consists of multiple
  semicolon-delimited numbered paragraph entries, lowering emits one typed
  schedule-entry insert per entry and chains after-anchors through the inserted
  predecessor so replay preserves source order rather than repeatedly inserting
  after the original anchor. Current witness: `ukpga/2000/17`
  `Sch. 15 para. 26(2)` affected by `ukpga/2008/9`
  `Sch. 11 para. 2(b)`, which inserts paragraph 30A, 30B, and 30C after the
  direct entry for paragraph 30.
  A schedule-partition lowering witness is `asp/2002/11` affected
  `sch. 2 Pt. 2` by `asp/2025/11` `sch. 4 para. 2(2)(a)`; corpus replay
  currently keeps that row visible as `uk_replay_schedule_list_entry_anchor_unresolved`
  when the carrier has no direct entries at the operation point.
  The source classifier also treats `for the entry relating to X substitute Y`
  as this same bounded list-entry frontier rather than a free text replacement.
  The source typo `after the entry relation to X insert Y` lowers through the
  same typed entry-insert family only when the rest of the instruction supplies
  an explicit before/after anchor and payload; the selector records
  `source_anchor_form=entry_relation_to_typo`.
- Affecting-source references of the form `Sch. N Pt. M` are hierarchical
  schedule-part references, not compound gateway/payload references. If exact
  extraction cannot resolve the schedule-scoped part, source extraction must
  block with `uk_affecting_act_schedule_part_standalone_split_rejected`; it may
  not split to standalone `Pt. M`, because that can select a main-body Part with
  the same label and contaminate payload extraction. Current witness:
  `ukpga/2020/17` affected `Sch. A1` by `ukpga/2021/11 Sch. 1 Pt. 1`, where
  exact extraction finds the Schedule A1 `BlockAmendment` and must not be
  overridden by the main Act `Part 1`.
- `for the entry relating to X substitute Y` is not a word-level text patch
  over the schedule carrier. It lowers to
  `uk_effect_schedule_list_entry_replace`: the canonical action remains
  `REPLACE`, the target remains the schedule carrier, and provenance carries
  a source-owned entry anchor plus replacement entry text. Replay must resolve
  exactly one direct `schedule_entry` child before replacing that child. As
  with entry insertions, an anchor may resolve through a unique longer-entry
  prefix or a unique leading-article-normalized match, and replay records the
  nonblocking normalization before applying the replacement. Missing or
  duplicate anchors block with
  `uk_replay_schedule_list_entry_replace_unresolved`, while successful entry
  replacement records `uk_replay_schedule_list_entry_replace_resolved`.
  Current witness: `asp/2000/7` affected `sch. 3` by `asp/2012/3 Sch. 2
  para. 4`.
- Some UK schedule-list carriers bucket entries under immediate schedule child
  groups such as `p1group` headings rather than as direct schedule children.
  When an explicit `before`/`after` entry-insert selector cannot resolve a
  direct schedule-entry anchor, replay may search only immediate schedule child
  groups and proceed only if exactly one descendant `schedule_entry` anchor
  matches. The new entry is inserted into that same group next to the anchor
  and replay records `uk_replay_schedule_list_entry_group_anchor_resolved`.
  Duplicate grouped anchors still block with
  `uk_replay_schedule_list_entry_anchor_unresolved`; this rule does not guess a
  group for alphabetical placement or tolerate lexical changes such as
  `Highland`/`Highlands` or `Crofting`/`Crofters`. Current witnesses:
  `asp/2010/8` affected `sch. 5` by `asp/2012/8` and `ssi/2013/192`.
- If a schedule-list-entry insert selector targets a schedule that replay has
  already repealed, replay classifies the row as
  `uk_replay_repealed_target_gap` with reason
  `schedule_target_previously_repealed` rather than reporting an anchor lookup
  failure. Current witness: later `asp/2010/8` Schedule 5/6 insert rows after
  the schedule expiry/repeal.
- If a structural repeal targets a descendant of a provision already repealed
  earlier in the replay chain, replay records nonblocking
  `uk_replay_repeal_target_already_absent_observed` instead of a target gap.
  This is only an idempotent structural-repeal observation; inserts,
  replacements, and text patches under a repealed prefix remain blocking.
  Current witness: `asp/2000/4`, where `asp/2001/8` repeals `s. 38` and
  `asp/2003/13` Sch. 5 Pt. 1 later repeals `s. 38(4)`.
- schedule-root repeals whose source text only claims `entry`/`entries` repeal
  must not delete the whole schedule. Replay prepare blocks this granularity
  escalation with `uk_replay_schedule_entry_repeal_granularity_blocked`.
  The same block applies to schedule partition carriers (`part`, `chapter`, or
  `division`) when source text only claims entry-level omission/repeal and no
  owned entry/paragraph target was lowered.
  Explicit source forms such as `the entry relating to X is repealed`,
  `omit the entry for X`, and `the entries for X, Y and Z are repealed` lower instead to
  `uk_effect_schedule_list_entry_repeal`: the target remains the schedule
  carrier, but a provenance selector lists the claimed direct entry anchors.
  Heading-only targets are excluded from this selector: a source such as
  `In the Part heading, omit "requirement"` is a heading-facet text patch, not
  authorization to delete a schedule/list entry named `requirement`.
  Replay must resolve every anchor to exactly one direct `schedule_entry` child
  before deleting any child. Missing, duplicate, or colliding anchors block
  atomically with `uk_replay_schedule_list_entry_repeal_unresolved`; successful
  entry-level deletion records `uk_replay_schedule_list_entry_repeal_resolved`.
  When a partition-carrier target is paired with explicit source wording such
  as `omitting the entry ... (numbered 86)`, lowering records
  `uk_effect_numbered_schedule_entry_repeal_target_refined` and refines the
  target to the numbered paragraph under that carrier. This is a target
  narrowing rule, not a replay fallback: without the explicit number, LawVM
  blocks rather than deleting the whole carrier.
  Partition-carrier targets may also carry explicit entry anchors, e.g.
  `in Part 2, the entry relating to the Deer Commission for Scotland is
  repealed`. These lower through the same schedule-list-entry selector, but
  replay resolves the anchor only inside the explicit carrier and deletes the
  uniquely matched paragraph entry; missing or duplicate matches block. For
  partition-carrier repeals, replay owns two narrow anchor normalizations:
  `uk_replay_schedule_list_entry_repeal_numbered_anchor_normalized` lets an
  anchor such as `79 National Consumer Council` match only a paragraph entry
  whose visible number is stripped from source text, and
  `uk_replay_schedule_list_entry_repeal_parenthetical_paragraph_normalized`
  lets an anchor such as `the Scottish Qualifications Authority (paragraph 49)`
  match only paragraph `49` when the stripped entry text is unique.
  Table-backed schedule lists are a separate owned shape:
  `uk_effect_schedule_list_entry_table_rows_lowered` preserves an actual
  `BlockAmendment/Tabular/table` payload as source-owned row IR when source
  text says e.g. `after the entry relating to NHS Education for Scotland
  insert`. Replay emits
  `uk_replay_schedule_list_entry_table_rows_insert_resolved` only when the
  target schedule has exactly one direct table, no direct schedule-entry
  children, and the first-column anchor resolves uniquely; missing, duplicate,
  non-table, or flattened-only payload cases block under
  `uk_replay_schedule_list_entry_table_rows_insert_unresolved`. A narrow
  anchor normalization is owned by
  `uk_replay_schedule_list_entry_table_anchor_citation_short_title_normalized`:
  when the source anchor spells out a same-context UK Act title such as
  `the Local Government (Scotland) Act 1973`, replay may compare it with a
  table row that abbreviates the same citation as `the 1973 Act`; the row must
  still be unique, and the resolved adjudication records the normalization rule.
  End-of-schedule table appends are owned separately:
  `uk_effect_schedule_table_end_rows_lowered` fires only when the source text
  explicitly says `at the end of schedule N ... insert` and the source payload
  is an actual `BlockAmendment/Tabular/table`. Replay then emits
  `uk_replay_schedule_table_end_rows_insert_resolved` only when the target
  schedule resolves to exactly one direct table and no direct schedule-entry
  children; missing tables, non-table payloads, flattened text-only payloads,
  or mixed carriers block under
  `uk_replay_schedule_table_end_rows_insert_unresolved`. This is not a generic
  words-inserted recovery and must not synthesize schedule paragraphs from
  table-cell prose.
  Enacted-source schedule table row recovery is a separate source-lane rule:
  `uk_affecting_act_enacted_schedule_table_row_source_extracted` may fire when
  current affecting XML is unavailable, the official enacted source exposes the
  broad affected Schedule, the effect target is one added schedule paragraph,
  and exactly one table row under exactly one source Part has a first-cell label
  matching that paragraph. LawVM synthesizes only a single `P1` paragraph
  payload from that row and records the source row text, Part label, and enacted
  locator; it must not admit the whole schedule as payload. Lowering then emits
  `uk_effect_enacted_schedule_table_row_part_target_refined` to refine the
  metadata target from `schedule:N/paragraph:X` to the source-owned
  `schedule:N/part:P/paragraph:X`; ambiguous or missing rows remain blocked as
  missing source payloads. Current witness: `asp/2002/13` Schedule 1 additions
  by `ssi/2008/297 Sch. 1`.
  Current witnesses: `asp/2000/7` affected `sch. 3` by `asp/2002/3`,
  `asp/2005/6`, and `asp/2010/8`; partition refinement witness:
  `asp/2002/11` affected `Sch. 2 Pt. 2` by `ssi/2002/468 art. 2`;
  partition anchor witness: `asp/2002/11` affected `sch. 2 Pt. 2` by
  `asp/2010/8 sch. 1 para. 29`; table-backed insertion witnesses:
  `asp/2002/11` Schedule 5 by `ssi/2020/5 art. 3(7)` and `asp/2023/6
  Sch. 2 para. 1(3)`.
- definition-anchor text patches may include the article before the quoted
  term, e.g. `after the definition of the "2002 Act" insert- ...`; the article
  is drafting syntax, not part of the definition key, so the selector remains
  `TEXT_AFTER_DEFINITION_2002 Act`. Current witness: `asp/2007/3` affected
  `s. 221` by `ssi/2012/301` `Sch. para. 3(2)(a)`.
- `after paragraph/sub-paragraph/subsection (X) insert- <new sibling block>` is
  a structural sibling-insertion family, not a child-text append. LawVM lowers
  the bounded source-owned form to `uk_effect_structural_sibling_insert_lowered`
  only when the effect-feed target is the parent container and the source names
  both the existing anchor child and the inserted child label. Example:
  `ukpga/2020/17` `Sch. 10 para. 1`, affected by `ukpga/2022/32`
  `Sch. 14 para. 12(2)(a)`, lowers `after paragraph (a) insert- aa ...` to an
  `insert` at `schedule:10/paragraph:1/item:aa`. The existing
  `TEXT_AFTER_CHILD_*` text patch remains valid only for inline insertions into
  the named child, not for creating siblings.
- Deictic and mixed block forms such as `after that paragraph, insert- <new
  sibling block>` and `at the end of paragraph (b), insert- <punctuation plus
  new sibling>` are the same unsupported structural-sibling family until a
  source-context compiler owns the antecedent, inserted child payloads, and any
  punctuation-only mutation separately. They must not fall through as generic
  parser gaps or become text appends to the enclosing subsection. Current
  witness: `asp/2001/2` affected `s. 82(1)` by `ssi/2024/161 art. 6(2)(b)-(c)`.
- `before/after sub-paragraph (i) insert- <new sibling block>` inside an
  inserted or nested paragraph is an amendment-program family, not an ordinary
  base-tree structural-sibling insert. For the narrow source-owned form
  `in sub-paragraph (N)(a), in the inserted paragraph (d), before/after
  sub-paragraph (i) insert- <label> <text>`, LawVM lowers to
  `uk_effect_amendment_program_inserted_parent_child_insert_text_patch` with a
  synthetic selector such as
  `TEXT_AMENDMENT_PROGRAM_INSERTED_PARENT_d_BEFORE_i`. Replay may apply it only
  inside the explicit amendment-instruction target after proving the inserted
  parent label and child anchor label are unique in that target text; success
  emits `uk_replay_amendment_program_inserted_parent_child_insert_applied`.
  The rule does not create base-law children and does not authorize unrelated
  live-tree structural sibling insertion. Rows matching the shape but lacking
  the target context, inserted-parent label, anchor label, or payload remain
  blocking `uk_effect_amendment_program_inserted_parent_structural_insert_rejected`.
  Current witnesses: `ukpga/2020/17` affected `Sch. 22 para. 21(2)(a)` and
  `Sch. 22 para. 21(3)(a)` by `ukpga/2022/32` `Sch. 14 para. 14(2)(a)-(b)`.
- Conditional expiry/repeal formulas such as `Paragraph 4 is repealed at the
  end of 2021 if, or to the extent that, it has not been brought into force`
  are temporal/applicability instructions, not unconditional current-text
  repeals. LawVM classifies them as
  `conditional_temporal_repeal_unsupported` and manual-frontier rule
  `uk_manual_frontier_conditional_temporal_repeal_out_of_scope`; the existing
  lowering rejection remains blocking so strict mode does not silently delete
  text. Current witness: `ukpga/2020/18` affected `Sch. para. 4` by
  `ukpga/2020/18` `Sch. para. 5`.
- Definition-child substitutions that also consume the child tail, for example
  `for paragraph (d) of the definition of "NHS body in England" and the "or"
  at the end of that paragraph substitute ...`, lower under
  `uk_effect_definition_child_and_tail_substitution_text_patch`. This is
  permitted only as a bounded definition-child replacement:
  `TEXT_DEFINITION_CHILD_PARAGRAPH_<term><US><label>`. It does not authorize a
  broad subsection rewrite or a separate unowned deletion of the connector.
  The lowering observation records the claimed tail connector. Source rows
  that match the family but fail to lower remain
  `definition_child_and_tail_substitution_unsupported` with manual-frontier
  rule `uk_manual_frontier_definition_child_and_tail_substitution_candidate`.
  Current witness: `ukpga/2021/17` affected `s. 15(7)` by `ukpga/2022/31`
  `Sch. 4 para. 239`.
- definition-scoped all-occurrence insertions such as `in the definition of
  "X" after "Y", in both places where it appears, insert "Z"` lower to
  `TEXT_IN_DEFINITION_X/AFTER_EACH/Y` using
  `uk_effect_in_definition_after_anchor_all_occurrences_insert_text_patch`.
  Replay rewrites all matching anchors inside the unique matching definition
  entry only; it must not broaden to the whole affected section. Current
  witness: `asp/2007/3` affected `s. 214(1)` by `ssi/2019/51`
  `reg. 6(2)(a)(ii)`.
- `from "X" to the end of paragraph (b) substitute "Y"` must not be broadened
  into a target-subtree `TEXT_FROM_X_TO_END` patch when the effect feed target
  names only the enclosing subsection. It also must not be narrowed to the
  labelled child alone, because the source range starts in the parent text and
  runs through child `(b)`. The owned lane is
  `uk_effect_labeled_child_end_range_text_patch`: the parser preserves the
  labelled child suffix, lowering keeps the compatible parent target, and
  replay applies `TEXT_FROM_CHILD_END/<kind>/<label>/<anchor>` only when the
  direct child endpoint exists uniquely and the parent text carries the start
  anchor plus the child-list tail separator. Successful replay emits
  `uk_replay_labeled_child_end_range_applied`. Current witnesses:
  `asp/2000/4` affected `s. 58(6)`, `s. 63(5)`, `s. 71(2)`, and `s. 74(2)` by
  `asp/2007/10 s. 60`.
- named table substitutions such as `for the Table A mentioned there
  substitute-` must not be smuggled through generic text replacement. They
  remain a table-compiler frontier until the table target and replacement
  surface are explicitly represented.
- manual-frontier classification treats dash-punctuated verbs such as
  `insert-` / `substitute-` as explicit instruction text. A row with such text
  and a blocking overlap-lowering record should remain a deterministic frontend
  candidate until the parser/executor family is either implemented or ruled out
  with a narrower source-pathology finding.
- source text like `in paragraph 17 ..., in sub-paragraph (a), for the
  inserted text substitute-` is not an ordinary replacement against the current
  base statute. When the source paragraph and subparagraph exactly match the
  feed target, UK lowers this through
  `uk_effect_amendment_inserted_text_substitution_text_patch`: replay rewrites
  only the target amendment instruction's payload after its own `insert-` verb.
  If the source context does not match the feed target, the row remains an
  amendment-program frontier instead of becoming a base-law text guess.
- source text that targets a table entry or table column remains manual unless
  the claim/compiler owns the row/cell model. The deterministic exceptions are
  explicit ordinal-column row insertion, for example `after the third entry in
  the second column relating to X insert- Y`, and explicit single-entry table
  anchors of the form `after the entry in the table relating to X insert- Y`
  or `after entry 4 in the table insert-` when the source carries exactly one
  `BlockAmendment` table-row payload. When the affected target itself names a
  table, explicit numbered anchors such as `after entry 6A insert-` are also
  admitted, but only as source-table payload insertions; the compiler preserves
  all rows from the `BlockAmendment` table and never fabricates rows from the
  flattened instruction text. It also admits subsection-targeted
  `after the entry for X insert-` only when the affected subsection is replay-
  proven to be backed by exactly one table and the source carries a
  `BlockAmendment` table payload; replay treats `X` as a table-entry group
  heading and inserts all source rows after that group, not after the first
  physical row. In all cases lowering emits
  `uk_effect_table_entry_row_insert`, carries a table-row selector, and replay
  must resolve exactly one table, expand rowspans, and insert a row payload
  after the selected physical row. Ordinal-column selectors count only entries
  in the named column whose earlier columns match `X`; relating-entry selectors
  require exactly one row whose cell text matches `X`; entry-label selectors
  require exactly one row whose first logical cell is the explicit anchor label.
  Deictic `after that entry insert-` may also carry a single logical entry
  group encoded as multiple physical source rows, but only when the first source
  cell rowspans across every source row; replay records
  `source_payload_mode=logical_table_entry_group` and inserts after the matched
  anchor cell's whole rowspanned group rather than after its first physical row.
  Ambiguous tables/rows emit `uk_replay_table_entry_row_insert_unresolved`.
  Source-owned between-column insertions such as `between the second and third
  columns, insert-` may lower under `uk_effect_table_column_insert` only when
  the source carries exactly one inserted table column. Replay resolves exactly
  one table, proves the visual column boundary, inserts one physical cell into
  each aligned row, and may only widen an owned header cell that spans across
  the insertion boundary; unresolved boundaries emit
  `uk_replay_table_column_insert_unresolved`. Other table-entry claims such as
  source-context-free `after that entry insert-`, flat numbered-entry payloads
  without a source table row, and appropriate-place table insertions remain
  `table_entry_target_unsupported`. The manual frontier classifier separates
  deictic row placement, column insertion, appropriate-place table placement,
  and generic table-entry candidates so future compilers can have distinct
  proof obligations. Replay must not flatten any table amendment into the host
  provision body just to remove a benchmark residual.
- schedule-list table-row insertions may inherit their anchor from the parent
  instruction when the extracted source element is only the `BlockAmendment`
  table payload. The parent must explicitly say `before/after the entry
  relating to/for X ... inserted`; lowering then reuses
  `uk_effect_schedule_list_entry_table_rows_lowered`, records the parent id in
  the selector, and replay still resolves the anchor against exactly one target
  schedule table before inserting source-owned rows.
- if such wording appears while metadata names only a broad schedule, part, or
  provision target, lowering emits
  `uk_effect_table_entry_instruction_rejected` instead of coercing the row into
  a host `repeal` / `replace`. Current corpus witness:
  `asp/2000/2` affected `Sch. 2 Pt. 2` via `ssi/2001/68 art. 2(4)` says
  `in column 1 of the table, in entry number 1 ... is omitted` and separately
  substitutes an amount; neither instruction authorizes repealing the whole
  schedule part.
- Broad schedule/part targets with explicit `in column N` wording may lower
  under `uk_effect_table_column_text_patch` only when the source text also
  carries an explicit text patch preimage. Replay then requires exactly one
  direct table under the broad target and exactly one cell in the named column
  containing that preimage before mutating cell text. Entry-number and
  multi-entry instructions remain rejected/manual until they have a row model.
  Because the row-level effect inspector currently compares broad schedule text
  surfaces rather than table-cell surfaces, those deterministic table-cell ops
  are classified as `table_cell_text_patch_requires_table_surface` instead of
  `uk_manual_frontier_text_patch_preimage_chain_gap`. That classification is an
  evidence-surface limitation, not permission to replay against the schedule
  body or to treat a named column as a missing source chain.
- source targets such as `Sch. 8 Note 1` are schedule-note/facet claims, not
  schedule paragraph claims. Lowering emits
  `uk_effect_schedule_note_target_rejected` until a note compiler or manual
  claim can target the note surface directly. Current corpus witness:
  `asp/2000/5` affected `Sch. 8 Note 1` via `asp/2003/9 Sch. 13 para. 17`;
  the old parser shape `schedule:8/paragraph:note/subparagraph:1` is rejected
  because it invents legal structure not present in the source target.
- reference-only extracted fragments such as `paragraph 1(1);` or
  `section 15(2)(b) (...)` are source-insufficient when paired with blocking
  word-level lowering. They must not remain unclassified manual frontier rows,
  because the public witness does not contain an executable amendment program.
- context/header-only extracted instruction rows such as `1 In section 183A of
  the Broadcasting Act 1990-` are also source-insufficient when paired with a
  blocking word-level lowering rejection. They may name the broad source
  context, but they do not carry the child instruction or payload needed to
  prove a text patch. Manual frontier reports
  `uk_manual_frontier_instruction_header_source_insufficient` instead of a
  generic unclassified row. Current witness: `ukpga/1990/42` affected
  `s. 183A(4)` and `s. 183A(6)(b)` by `ukpga/2016/11 s. 54(1)(a)-(b)`.
  Quoted object fragments and definition-target fragments retain their more
  specific classifications before this header fallback:
  `uk_manual_frontier_effect_metadata_carried_text_patch_candidate` and
  `uk_manual_frontier_definition_target_fragment_source_insufficient`.
  A quoted definition-entry payload paired only with word-insertion effect
  metadata is not a deterministic text-patch candidate unless source context
  supplies an insertion anchor. It is classified as
  `uk_manual_frontier_appropriate_place_definition_entry_candidate`, reusing
  the definition-entry placement claim template.
- quoted word-repeal object fragments whose official effect metadata supplies
  `words repealed`/`words omitted` may lower under
  `uk_effect_metadata_carried_quoted_words_repeal_text_patch` when the source
  row carries exactly one quoted word/phrase and does not independently scope
  that phrase to a subsection/paragraph range. This
  is an effect-feed elaboration rule: metadata supplies the action and affected
  target, source supplies the quoted preimage. It must not override more
  specific child-qualified omission rules where the quoted phrase is followed
  by `in paragraph ...` / `in sub-paragraph ...`.
  Prefix target forms such as `in subsection (1)(a), the words "X"` and
  `in sub-paragraph (1)(a), the words "X"` / `in sub-paragraph (2), the words
  "X"` are owned by
  `uk_effect_child_qualified_word_omission_text_patch`; lowering verifies the
  source parent/child labels against the effect target before emitting a text
  repeal. The same rule may handle conjoined source rows such as `In
  subsection (3), paragraph (a) and in paragraph (b) the words "X" shall cease
  to have effect` for the word-repeal effect targeting paragraph `(b)`, while
  recording the sibling label as context rather than deleting the sibling.
- structural child-range substitutions such as `for paragraphs (a) and (b)
  there shall be substituted "..."` are classified as
  `uk_manual_frontier_structural_child_range_substitution_candidate` while no
  compiler owns the removed child identities and replacement payload shape.
  Deictic text patches such as `for those words in the second place where they
  occur substitute "..."` are classified as
  `uk_manual_frontier_deictic_text_patch_source_insufficient` until the
  antecedent is source-proven. Definition-child substitutions such as `in the
  definition of "D", for paragraph (a) ... substitute- ...` are classified as
  `uk_manual_frontier_definition_child_structural_substitution_candidate`
  because the compiler must own the definition child and connector/tail
  semantics. Current witnesses are the remaining `ukpga/1990/42`
  `uk_manual_frontier_parser_or_extraction_candidate` rows.
- Effect-feed target references may use the undotted abbreviation `para` as
  well as `para.`. UK target parsing treats both as `paragraph` under
  `uk_target_ref_undotted_para_abbreviation_normalized`; otherwise
  `Sch. 1 para 7(1)(b)` is mis-addressed as
  `schedule:1/paragraph:para/subparagraph:7/item:1/item:b` rather than
  `schedule:1/paragraph:7/subparagraph:1/item:b`. This is grammar
  normalization at parse time, not a replay fallback or live-tree retargeting.
- source text that uses a table as an amendment program, for example
  `provisions listed in column 1 ... for the words in the corresponding entry
  in column 2 ... substitute "X"`, may lower under
  `uk_effect_corresponding_table_entry_word_substitution` only when the
  affecting XML source root contains a unique table row whose column 1 mentions
  the affected provision and whose column 2 supplies the old words. Row-span
  carried cells are part of the source-table elaboration. Only source tables
  whose header exposes `Column 1` and `Column 2` participate; explanatory
  chronology tables such as `Provision / Date of commencement` are not valid
  sources for this family. Descendant labels must belong to the same section
  expression in the column-1 row, so `s. 39(3)` may not be satisfied by a row
  that names `s. 39(5)` and separately names `s. 43(3)`. If the table context
  is unavailable or the row match is not unique, lowering emits
  `uk_effect_corresponding_table_entry_word_substitution_unresolved` and
  blocks in strict mode rather than guessing a text patch.
- source text that targets an existing base-table cell by ordinal column,
  ordinal entry, and a rowspanned relation cell, for example `in the second
  column, in the second entry relating to the Welsh Ministers, after "X" insert
  "Y"`, may lower under `uk_effect_table_entry_inline_text_insertion`. The
  lowered op is targeted at the containing provision and carries a structured
  table-cell selector in operation provenance; replay must resolve exactly one
  table under that provision, expand rowspans, count only entries whose earlier
  columns match the `relating to ...` witness, and mutate only the selected
  cell text. If the table selector is invalid, ambiguous, or the selected cell
  lacks the quoted preimage, replay emits a blocking
  `uk_replay_table_entry_inline_text_insertion_unresolved` or
  `uk_replay_table_entry_inline_text_preimage_gap` adjudication. This is a
  deterministic table compiler, not a fallback from a missing target path.
- source-carried child rows under a parent instruction like `the entry for the
  Information Commissioner is amended as follows` may lower under
  `uk_effect_source_carried_table_entry_paragraph_substitution_text_patch`
  when the child row explicitly substitutes a paragraph or subparagraph inside
  that entry. Lowering targets the broad schedule, carries a table-cell selector
  with the parent entry label, and uses a symbolic paragraph/subparagraph text
  selector. Replay may mutate only a uniquely resolved table cell; it resolves
  the flat cell paragraph shape at apply time and emits
  `uk_replay_source_carried_table_entry_paragraph_substitution_resolved` or a
  blocking table-cell unresolved/preimage adjudication. This is intentionally a
  table-cell compiler, not a fabricated `schedule/paragraph` target.
- direct table-marker word patches may lower to source-owned table-cell
  selectors when the source supplies enough row/cell evidence:
  `uk_effect_table_entry_relating_text_patch` for `in the entry relating to X,
  for "Y" substitute "Z"`, `uk_effect_table_entry_label_text_patch` for
  `in entry 1A in the table`, `uk_effect_table_entry_relating_column_text_patch`
  for `in the entry for X, in the Nth column`,
  `uk_effect_table_entry_label_column_text_patch` for `in entry X, in column Y`,
  `uk_effect_table_entry_labels_column_text_patch` for `in entries X and Y,
  in column Z`, `uk_effect_table_entry_deictic_label_column_text_patch` for
  `in that entry, in column Z` only when the previous source sibling explicitly
  names the table entry, and
  `uk_effect_table_column_heading_text_patch` for `in the heading of the
  second column`
  - replay mutates only a uniquely resolved table cell; ambiguous tables,
    ambiguous cells, or missing preimages remain blocking table-cell
    adjudications
  - plural entry/column patches are all-or-nothing: replay first resolves every
    named row/column cell and checks every preimage, then emits
    `uk_replay_table_entry_multi_cell_text_patch_resolved` only after all
    selected cells were mutated
  - `TEXT_END` appends inside a source-owned table-cell selector append to the
    selected cell only; replay must not append to the containing provision or
    table wrapper
  - `s. N(1) Table` may be carried by a section-level table in the source XML
    or by a subsection `(1)` table. Lowering records this as an implicit
    subsection-one table carrier in the selector rather than changing legal
    scope silently.
  - current witnesses: `ukpga/2020/17` `s. 174(1) Table` by `ukpga/2022/32
    Sch. 17 para. 4(3)(a)`, `s. 122(1) Table` by `ukpga/2022/32 Sch. 21
    para. 3(a)`, and `s. 166(5) Table` by `ukpga/2026/2 s. 7(9)(d)`
- Direct table-row inserts shaped as `after that entry insert- ...` may lower
  only when the previous source sibling explicitly identifies the table entry
  (for example `in the entry relating to X, for "Y" substitute "Z"`) and the
  current source row carries either exactly one table-row payload or exactly one
  logical table-entry group owned by a rowspanning first cell. The selector
  records the source-owned relation text plus any `Y`/`Z` row-anchor alternates
  from the sibling substitution; replay must still resolve exactly one table
  row or logical row group before inserting. Current witnesses:
  `ukpga/2020/17` `s. 174(1) Table` by `ukpga/2022/32 Sch. 17 para. 4(3)(b)`,
  `s. 190(3) Table` by `Sch. 17 para. 7(b)`, `Sch. 7 para. 27(6) Table` by
  `Sch. 17 para. 13(3)(b)`, and `Sch. 8 para. 11(4) Table` by
  `Sch. 17 para. 14(4)(b)`.
- UK source/oracle XML table structure is preserved by the grafter under the
  named family `uk_table_xml_structure_preserved`: `<Table>` / `<Tgroup>` /
  `<Thead>` / `<Tbody>` / `<Row>` / `<Entry>` become `table` / `row` /
  `header_cell` / `cell` IR nodes, and row cell text is not smuggled into the
  host provision's body text. This is only a source-shape preservation rule;
  it does not by itself authorize table row/cell amendments.
- UK replay-vs-oracle EID comparison drops replay-only table fallback nodes
  under `uk_replay_compare_table_fallback_identity_noise` when the oracle EID
  surface has no table EIDs. Table wording still participates through ancestor
  text; row/cell fallback identity is not a comparable benchmark surface until
  both sides expose stable table EIDs.
- source text that says `at the appropriate place, insert-` or `at an
  appropriate place, in alphabetical order, insert-` is classified as an
  explicit source-pathology frontier while UK lacks a safe placement model for
  that source shape. Generic placement rows use
  `appropriate_place_insert_unsupported` and
  `uk_manual_frontier_appropriate_place_candidate`. Definition-entry payloads
  such as `"X" means...` or `"X" is to be construed...` use the narrower
  `appropriate_place_definition_entry_insert_unsupported` and
  `uk_manual_frontier_appropriate_place_definition_entry_candidate`, because
  they are plausible semantic-compile work items but still lack a source-named
  insertion anchor. Index/list-entry payloads such as `"relevant register"
  paragraph 22B(6A)` use
  `appropriate_place_index_entry_insert_unsupported` and
  `uk_manual_frontier_appropriate_place_index_entry_candidate`; they are not
  definition-entry payloads and need a target list/index carrier plus an exact
  predecessor, successor, or ordering claim. It is not a generic parser miss
  because replay must not pick an insertion point by alphabetical coincidence,
  oracle order, or live-tree uniqueness. Lowering records definition-entry rows
  under `uk_effect_appropriate_place_definition_entry_insert_rejected` rather
  than the generic overlap-substitution parser-miss rule so work queues can
  route them to a placement-claim validator. A broad ancestor or sibling amendment
  formula that names a different definition term is not a valid placement
  claim for the current child row. Current witness: `asp/2001/2`
  `asp/2019/17 Sch. para. 3(6)(a)(iii)/(vi)/(viii)` and
  `Sch. para. 3(9)(a)(v)/(vii)`, which must remain five manual
  `uk_manual_frontier_appropriate_place_definition_entry_candidate` rows until
  a validated claim supplies exact placement.
- repeal schedules, table parts, or grouped repeal source fragments that expose
  an `Enactment / Extent of repeal`, `Provision / Extent of repeal`, or
  `Provision / Extent of repeal or revocation` surface but do not yet identify
  the specific target row/cell for the affected provision are classified as
  `repeal_schedule_table_source_unsupported` and
  `uk_manual_frontier_repeal_table_candidate`. This keeps the table witness
  visible without smuggling the whole repeal schedule into a single target.
- bounded repeal-table quoted-words rows are now owned by
  `uk_effect_repeal_table_quoted_words_text_repeal`: the compiler must match a
  unique repeal table row by affected Act identity, split only explicit extent
  clauses inside that owned row, match the clause to the affected provision, and
  lower `the word(s) "..."`, `the words from "X" to "Y"`, and
  `the words from "X" to the end` to `TEXT_REPEAL`. Range clauses lower to the
  existing bounded selectors `TEXT_FROM_X_TO_Y` / `TEXT_FROM_X_TO_END`, carrying
  any explicit ordinal occurrence such as `where they thirdly occur`.
  Parenthetical labels and years inside quoted payload/preimage text are
  ignored for target-scope matching; they are payload evidence, not authority to
  retarget the clause or reject the row as the wrong affected Act. Non-unique
  rows, whole-provision repeal clauses, entry/table structural repeals, and
  multi-action clauses remain blocking
  `uk_effect_repeal_table_quoted_words_text_repeal_unresolved` / manual
  frontier cases. This is a source-table elaboration rule, not a replay
  fallback, and its observation records the enactment cell, extent clause, and
  selected quoted/range preimage plus occurrence metadata.
- bounded repeal-table whole-provision rows are owned by
  `uk_effect_repeal_table_structural_repeal`: the compiler must match the same
  unique affected-Act row and an extent clause that explicitly names the exact
  affected part, chapter, section, schedule, paragraph, subsection, or
  subparagraph. It then lowers only the feed-named target to a typed `REPEAL`.
  The enactment/provision column normally corroborates the affected Act by short
  citation such as `(asp 3)` or `(c. 9)`. If the source row omits that citation,
  the compiler may instead use an exact normalized `AffectedTitle`+year match
  from the effect feed; the lowering observation records
  `enactment_match_basis=exact_affected_title_year` so this source-lane
  recovery is visible.
  Clauses mentioning
  words, definitions, entries, or table-entry surfaces remain outside this
  rule; non-unique or unmatched rows emit
  `uk_effect_repeal_table_structural_repeal_unresolved` rather than replaying a
  broad repeal schedule or table row as host text. Manual frontier classifies
  unresolved repeal-table lowering records as
  `uk_manual_frontier_repeal_table_candidate` even when the source-pathology
  classifier did not pre-label the source, because the lowering record itself
  carries the table row, extent cell, and target-split evidence. A unique
  section-range cell such as `Sections 26 to 31.` may corroborate each
  feed-expanded numeric section target inside that range; a unique
  container-list cell such as `Parts 1 and 2.` may corroborate each
  feed-expanded part target inside that list. Targets outside the source-owned
  list or range remain unresolved.
  Repeal-table extent clause splitting treats `Part(s)` and `Chapter(s)` as
  structural clause starts, so a mixed cell such as `Section 92(3) and (6). In
  section 93(1) and (3), the words "...". Part 6.` can lower the `Part 6`
  repeal without being swallowed into the preceding word-level clause.
  A single extent clause that combines word-level repeals with a separately
  named structural target may lower only the exact structural target when the
  structural mention is grammatically separate after punctuation or `and`, for
  example `In section 142, in subsection (1), the words "...", the words "..."
  and subsection (2).` for effect target `s. 142(2)`. The lowering observation
  records
  `reason_code=mixed_structural_and_word_repeal_split_structural_target` and
  `split_from_mixed_extent_row=true`; the word-level lanes remain separate
  source facts and are not silently replayed. The same row must not lower a
  whole repeal for `s. 142(1)`, because `in subsection (1), the words ...`
  scopes only the word deletion. Mixed clauses without a separately named exact
  structural target, such as `Section 69(3)(b) and the word "and" immediately
  preceding it.`, remain blocked under
  `mixed_structural_and_word_repeal_requires_split` until lowering can emit both
  source lanes without dropping either. Manual claim templates expose that
  reason code and require `structural_and_text_repeal_split_boundary`
  ownership before a claim can become executable.
  Current witnesses: `asp/2000/6` / `asp/2006/8` schedule repeal of sections
  `26` to `31`; `asp/2001/8` / `asp/2010/8 sch. 14 para. 37` repeal of
  Parts `1` and `2`; `asp/2001/10` / `asp/2006/1 Sch. 7` repeal of Part `6`;
  separately named mixed-lane witness `ukpga/1992/8` /
  `ukpga/2002/19 Sch. 2`; blocked mixed-lane witnesses `asp/2001/2` /
  `asp/2008/1 Sch. 2` and `ukpga/2000/6` /
  `ukpga/2003/38 Sch. 2 para. 4(3)(a) Sch. 3`.
  Repeal-table elaboration may use the full affecting `source_root` when the
  extracted effect source is only a gateway provision such as `the enactments
  specified in Schedule 15 are repealed to the extent specified in the third
  column`; the matched schedule row still must identify the affected Act and
  exact target. Simple plural section lists such as `Sections 153 and 154.`
  corroborate each feed-expanded section target in that list. Blank enactment
  cells in continuation rows inherit only the previous non-empty enactment cell
  in the same source table; this is table-row source context, not authority to
  cross table boundaries. Direct prose clauses such as `the words "X" are
  repealed` must not search unrelated repeal tables elsewhere in the source
  root; those clauses continue through ordinary text-fragment lowering unless
  the extracted node itself contains the table or explicitly acts as a
  repeal-table gateway.
- bounded repeal-table definition-entry rows are owned separately by
  `uk_effect_repeal_table_definition_entry_text_repeal`: after the same unique
  affected-Act row and affected-provision clause matching, an extent clause of
  the form `the definition of "X"`, singular `the entry for "X"`, or a bounded
  quoted plural list `the entries for "X" and "Y"` lowers to existing
  `TEXT_DEFINITION_ENTRY_X` delete selectors. This rule does not authorize
  whole-provision repeals, table-entry repeals, unquoted/plural entry
  descriptions, or definition-child removals; those remain explicit
  manual/frontier cases until their target granularity is owned. If the effect
  feed gives a pseudo definition child target such as `s. 167(1) (defn. of
  "Joint Authority")` for a structural `repealed` effect, the compiler may
  lower to the owning provision only when the repeal-table extent cell
  explicitly names that definition entry. The lowering observation uses
  `reason_code=unique_repeal_table_extent_row_definition_entry_from_pseudo_target`
  and records both the original pseudo target and the owning provision target.
  Current witness: `ukpga/1992/8` / `ukpga/1998/47 Sch. 15` repeal of the
  `Joint Authority` definition in section 167(1).
- bounded repeal-table definition-child rows are owned by
  `uk_effect_repeal_table_definition_child_text_repeal`: after the same unique
  affected-Act row and affected-provision clause matching, an extent clause of
  the form `in the definition of "X", paragraphs (b) and (c)` lowers to
  separate `TEXT_DEFINITION_CHILD_PARAGRAPH_X<US>b` and
  `TEXT_DEFINITION_CHILD_PARAGRAPH_X<US>c` delete selectors. This is a
  definition-child source-table elaboration rule, not a quoted-word fallback:
  effect metadata may say `words repealed`, but the source row owns child
  deletion. Nested labels such as `paragraph (a)(iii)` remain unresolved until
  nested definition-child identity is explicitly represented. Current witness:
  `ukpga/1992/8` / `ukpga/2002/21 Sch. 6` repeal of definition paragraphs
  `(b)` and `(c)` for `income-related benefit`.
- table-entry source that says a named entry/column is `added` or `amended`
  remains in `table_entry_target_unsupported` until a table compiler owns the
  row/cell and any referenced amount schedule. It is not a generic parser miss.
- Embedded tables inside a structural paragraph substitution payload are not
  table-entry instructions. If the target is a paragraph, the source text says
  `for paragraph X substitute`, and table/column words occur only inside the
  replacement payload, lowering records
  `uk_effect_embedded_table_payload_structural_substitution_preserved` and lets
  normal structural payload extraction continue. Current witness:
  `ukpga/2020/17` affected `Sch. 21 para. 5A 6` by `ukpga/2022/32 s. 127`.
- Direct table targets whose source says `after that entry insert- ...`,
  `after entry 6A insert- ...`, `after the entry in the table relating to ...`,
  or `at the appropriate place insert- ...` inside a table are the same
  table-entry frontier unless the row/cell compiler proves the antecedent and
  payload. Direct `between the second and third columns, insert- ...` claims
  are supported only by the guarded one-column table payload lane above.
  Lowering emits blocking `uk_effect_table_entry_instruction_rejected` or, for
  guarded row-insert selectors whose source lacks a single row payload,
  blocking `uk_effect_table_entry_row_insert` with an entry-shape witness. This
  is only diagnostic ownership; replay still requires a typed table row/cell
  model before mutating legal text.
  Current blocked witness: `ukpga/2020/17` `s. 379(1) table` by
  `ukpga/2023/41 Sch. 13 para. 11` carries a real two-row table payload for
  the Northern Ireland Troubles (Legacy and Reconciliation) Act 2023, but the
  source says only `at the appropriate place`. The oracle places it after
  Public Order Act 2023; LawVM must not derive that placement from the oracle or
  from coincidental table order until a source/claim-backed ordering model
  proves the row boundary.
- source text shaped as `shall have effect as if ...` is classified as
  `as_if_application_modification_unsupported` and manual frontier
  `uk_manual_frontier_as_if_application_modification_out_of_scope`. That family
  is an applied/as-if modification lane, not a direct mutation of the base
  statute text/tree under the current UK replay model. If source-local lowering
  first emitted `uk_effect_lowering_no_supported_action_rejected`, the
  replay-oriented caller reclassifies that diagnostic as nonblocking under
  `uk_effect_nonreplay_lowering_observed` once source-pathology classification
  proves the out-of-scope as-if lane.
- Source text that applies or invokes another Act's rules by reference is also
  non-textual under the structural replay lens. Empty-effect rows with no
  supported action are classified as
  `application_by_reference_effect_out_of_scope` when the source says another
  rule set `shall ... have effect ... for the purpose of ...`, a referenced
  Act `shall have effect`, named sections/parts of a referenced Act `shall
  apply`, a referenced rule set `shall apply as if ...`, or compensation
  disputes are to be determined under a named Act. Manual frontier reports
  `uk_manual_frontier_application_by_reference_out_of_scope` rather than
  generic unsupported-effect-family. This is not a source-insufficient text
  mutation: the source is visible, but it does not amend the target Act's
  text/tree. Current witness: `ukpga/1961/33` rows affected by railway/harbour
  instruments such as `wsi/2001/2197 Sch. 2 para. 5(2)`,
  `wsi/2001/2197 Sch. 2 para. 6(4)`, `uksi/2001/3682 art. 16(5)`, and
  `uksi/2002/1064 art. 17(3)`.
- `BlockAmendment` fragments that expose only payload text while the feed says
  `words substituted` or another word-level effect are classified as
  `payload_fragment_without_action_formula` and
  `uk_manual_frontier_source_pathology_insufficient`. This is source
  insufficient: the fragment may be legally relevant payload, but it does not
  contain the operative formula needed to prove the preimage/replacement pair.
  Numbered block payloads such as `1 A licence ...` are included when the
  leading number is a payload label rather than a numbered formula like
  `1 In section ...`; the latter remains `unhandled_instruction_text`.
  Current witness: `asp/2000/5` affected `s. 73(1)` by `asp/2003/9`
  `Sch. 13 para. 13(a)(iii)` and `ukpga/1990/42` affected `s. 53(1)` by
  `ukpga/2003/21 Sch. 15 para. 25(2)`.
- Source text such as `the words "X", where they occur in subsections (1) and
  (2), are repealed` lowers through
  `uk_effect_source_carried_multi_subunit_repeal_text_patch` when the source
  section number matches the feed target. Replay uses
  `TEXT_IN_CHILDREN_subsection_<labels><US><quoted text>` and mutates only the
  named direct child subsection text fields. It must not replay as a
  section-wide deletion. Current witness: `asp/2000/4` affected `s. 22` by
  `asp/2007/10 s. 57(6)`.
- Source text such as `in subsection (5), the words following paragraph (b)
  are repealed` lowers through
  `uk_effect_source_carried_child_tail_repeal_text_patch` when the feed target
  names that exact subsection. Replay uses the synthetic selector
  `TEXT_AFTER_CHILD_TAIL_paragraph_<label>` and may trim only collapsed parent
  tail text when the named paragraph is unique and last among direct children;
  it must not delete sibling provisions or pick a parent body by approximate
  text anchoring. Current witness: `asp/2000/1` affected `s. 21(5)` by
  `ssi/2013/177 Sch. para. 4(a)`.
- The equivalent imperative form `in subsection (1), omit the words after
  paragraph (b)` lowers through the same rule and selector, with the same exact
  containing-subsection check. Current witness: `ukpga/1990/42` affected
  `s. 56(1)` by `ukpga/2024/15 Sch. 4 para. 3(2)`.
- Source text such as `in paragraph (a), the words following sub-paragraph
  (ii) are repealed` lowers through
  `uk_effect_source_carried_subparagraph_tail_repeal_text_patch` when the feed
  target names that exact paragraph. Replay uses the same bounded child-tail
  selector family, `TEXT_AFTER_CHILD_TAIL_subparagraph_<canonical label>`, and
  may trim only the collapsed parent paragraph tail after a unique last direct
  subparagraph. This does not authorize omitted wording, parent fallback,
  whole-paragraph rewrites, or broader substitution/multi-subunit cases.
- The related form `for the words after paragraph (b) substitute "..."` lowers
  through `uk_effect_source_carried_child_tail_substitution_text_patch` when
  the source subsection matches the feed target. Replay uses the same bounded
  `TEXT_AFTER_CHILD_TAIL_paragraph_<label>` selector and may replace only the
  collapsed parent tail after the last direct child. Unlike deletion, a
  substitution may replace non-connector tail text such as `for more than ...`
  because the source explicitly supplies replacement text for the whole tail;
  it still blocks instead of treating the row as a whole-subsection text patch
  when the child anchor is absent, non-unique, or not the last direct child.
- `BlockAmendment` payload fragments with structured list payload such as
  `the Parliamentary corporation- a after ...; and b with ...` are also
  classified as `payload_fragment_without_action_formula` when the operative
  formula is absent from the extracted source. Current witness: `asp/2000/7`
  affected `s. 8(3)` by `asp/2010/11 Sch. 2 para. 1(a)`.
- Payload-only `BlockAmendment` fragments whose parent source instruction
  supplies the anchor but whose payload visibly introduces child structure are
  triaged as
  `uk_manual_frontier_source_carried_structured_text_patch_candidate` until the
  source-carried structure is owned. These rows are not safe flat text rewrites:
  a compiler or manual claim must combine the parent formula with the structured
  payload and preserve the child boundary.
  - A source-carried quoted substitution is now owned when the parent formula
    says `for [the word(s)] "X" there is substituted` and the `BlockAmendment`
    payload carries a consecutive roman child run under an optional dash-ended
    parent prefix such as `to— i ... ii ...`. Lowering emits
    `uk_effect_source_carried_quoted_text_substitution_text_patch` using the
    parent preimage and source payload; replay may materialize the visible child
    labels with `uk_replay_source_carried_labeled_child_text_substitution_recovered`
    and records the carried parent prefix. Current witnesses: `asp/2000/11`
    affected `s. 11(4)(b)` by `asp/2006/10` Sch. 6 para. 9(4)(c)(ii), and
    `asp/2002/3` affected `s. 5(2)(a)` by `asp/2005/3` Sch. 5 para. 7(3)(a).
  Remaining manual witnesses:
  - `asp/2001/10` affected Sch. 6 para. 2 by `asp/2004/8` Sch. 4 para. 6(3):
    parent gives the substituted preimage; payload contains an `a`/`b` list.
  - `asp/2001/2` affected `s. 82(1)` by `asp/2005/12` s. 51(8)(c): parent says
    `after "authority" there is inserted`; payload contains `; or` plus a new
    child `b` row.
- A narrower payload-only `BlockAmendment` family is deterministic when the
  source-local parent instruction explicitly substitutes a bounded sibling
  range and the payload owns a contiguous front of that replacement range.
  Lowering records `uk_effect_source_parent_substitution_range_payload_lowered`,
  emits one replacement per payload sibling, and emits explicit repeals for
  any trailing source-named siblings not present in the payload. Replay may
  then use `uk_replay_schedule_item_target_from_parent_substitution_resolved`
  to resolve a feed shape such as `Sch. 1 para. (d)` to a unique schedule item
  `(d)` only for operations carrying that lowering witness. Strict mode should
  block that target recovery; quirks replay may apply it with the adjudication.
  Current witness: `asp/2000/4` affected Sch. 1 para. (d) by `asp/2001/8`
  Sch. 3 para. 23(6), where the parent says `for paragraphs (d) to (g) there
  is substituted-` and the payload contains new item `(d)(i)-(iii)`. A second
  witness is `asp/2000/4` affected `s. 35(1)(a)-(e)` by `asp/2001/8` Sch. 3
  para. 23(2)(a), where the parent range is `(a)` to `(g)` and the payload
  owns replacement paragraphs `(a)` to `(e)`.
- A related payload-only `BlockAmendment` family is deterministic when the
  source-local parent instruction explicitly says `at the end there is added`
  or `inserted`, and the sole structural payload child exactly matches the
  metadata target kind and label. Lowering records
  `uk_effect_source_parent_at_end_added_payload_lowered` and emits a single
  `INSERT`; replay still requires the target to be absent and the parent to
  exist, so the parent instruction does not authorize target hijacking or
  replacement of an existing sibling. Current witness: `asp/2000/4` affected
  `s. 35(6)` by `asp/2001/8` Sch. 3 para. 23(2)(d).
- A source row that says `after paragraph (b), insert ; c ...; d ...; or e ...`
  is not a valid whole-row payload even though it contains instruction text.
  When the affected metadata explicitly says `(c)-(e) and semicolon`, lowering
  records `uk_effect_after_paragraph_insert_labelled_series_lowered`, emits a
  bounded `TEXT_END` semicolon append for paragraph `(b)`, and emits one
  labelled paragraph `INSERT` for each contiguous source sibling. The rule
  must prove the metadata range, source anchor, semicolon, and source labels;
  otherwise the row remains `instruction_text_reused_as_payload`. Current
  witnesses: `asp/2000/4` affected `s. 16(6)(c)-(e) and semicolon` and
  `s. 64(2)(c)-(e) and semicolon` by `asp/2006/4` s. 57(2)(b) and s. 57(3)(b).
- source text that targets `heading`, `title`, or `sidenote` facets lowers
  when it is an explicit word substitution/omission with a concrete old text
  selector, an explicit `at the end insert ...` append, or an explicit
  `after "X" insert "Y"` insertion against a quoted heading anchor. Explicit
  full-facet forms such as `the section heading becomes "X"`,
  `the title to the section becomes "X"`, and
  `the title of section N becomes "X"`, and
  `for the heading of Part N substitute "X"` lower as
  `uk_effect_heading_facet_full_replacement_lowered` with a `TEXT_ALL` selector
  only when the affected target itself is a heading/title/sidenote facet; this
  is not a fallback for ordinary section replacement. Replay then
  mutates only the heading carrier: direct heading text on title-bearing nodes, an
  explicit `heading` child under the target section, a unique `P1group`
  heading that wraps the target section, or a subordinate source `P2group` /
  `P3group` / `P4group` preserved as `pgroup` under
  `uk_parse_subordinate_pgroup_heading_carrier`. Inserted provisions wrapped in
  source `P1group/Title + P1` payloads preserve the wrapper title as a
  target-owned `heading` child under named payload-normalization observations:
  `uk_effect_inserted_section_p1group_heading_carrier_lowered` for sections and
  `uk_effect_inserted_p1group_heading_carrier_lowered` for schedule
  paragraphs. This rule is deliberately narrower than using a live parent
  `P1group`: shared parent headings for neighbouring provisions remain
  ambiguous. A multi-child `pgroup`
  heading may be used only for its first structural child, matching source
  wording such as "italic heading before subsection (3)"; later children must
  not hijack that carrier. Ambiguous shared wrappers emit
  `uk_replay_heading_facet_target_gap`. Other heading insertions and
  selector-less facet edits remain `heading_facet_target_unsupported` /
  `uk_manual_frontier_heading_facet_candidate` until LawVM has a typed
  placement compiler for them. Schedule heading targets with explicit quoted
  anchors use the same facet lane and do not enter the schedule-list-entry
  elaboration path. A heading-frontier source that says `before paragraph N of
  Schedule M (and the italic heading before it) insert- Part X <heading>` is
  not a simple facet rewrite: the manual claim template uses
  `schedule_part_wrapper_insertion` and must prove the anchor paragraph, the
  carried existing italic-heading boundary, whether following children move
  under the new Part wrapper, and any lineage/wrapper migration events.
  Current witness: `ukpga/2020/14` affected `Sch. 15 Pt. 1 heading` by
  `ukpga/2024/3 s. 12(3)(b)`.
- Table cross-heading targets are separated from ordinary cross-heading facets.
  A target such as `Sch. 6 para. 51 Table cross-heading` is classified as
  `table_crossheading_target_unsupported` /
  `uk_manual_frontier_table_crossheading_candidate` because the affected
  surface is a table heading cell or text prefix, not the host paragraph body
  or a normal cross-heading node. Claim templates use
  `table_crossheading_text_rewrite` and must prove the exact table carrier,
  heading-cell/prefix boundary, row boundary, and preservation of table
  entries. If the source says the cross-heading preceding `entry N` `becomes`
  quoted text, the template records that `becomes` payload and anchor instead
  of reusing unrelated later `for/substitute` entry patches.
  Current witnesses: `ukpga/2000/17` affected `Sch. 6 para. 51 Table
  cross-heading` by `uksi/2007/3538 Sch. 21 para. 27(2)(a)` and `Sch. 6
  para. 51(6) Table cross-heading` by `uksi/2010/675 Sch. 26 Pt. 1 para. 16`.
- Mixed targets of the form `s. N(X) and heading` may lower the structural
  insert under `uk_effect_mixed_heading_structural_insert_target_normalized`
  when the source carries an explicit inserted structural payload for `X`.
  The heading suffix remains visible as unresolved evidence
  (`heading_facet_status=unresolved`) and is not used as a body target. Compound
  metadata such as `s. 61(2A)(2B) and heading` is normalized only if the source
  payload proves sibling expansion; otherwise the row stays blocked as a heading
  facet candidate rather than synthesizing a nested `2A/2B` target. Witnesses:
  `ukpga/2020/17`, affected by `ukpga/2021/11 Sch. 13 para. 11(4)(b)`,
  `ukpga/2022/32 s. 159(2)`, and `ukpga/2022/32 Sch. 15 para. 3`. Source
  payloads may expose the inserted siblings directly or through a subordinate
  `P2group` / `P3group` / `P4group` heading carrier, but lowering still emits
  one structural operation per proved child and keeps the shared heading facet
  unresolved.
- Flat `BlockAmendment/P1para` schedule paragraph insert payloads may lower
  only when a direct source text run begins with the exact target paragraph
  label. The rule is
  `uk_effect_flat_p1para_schedule_paragraph_insert_payload_lowered`; sibling
  text runs such as cross-heading text are recorded as unresolved heading
  surface and are not smuggled into the inserted paragraph. If the effect
  metadata names a schedule Part but the source-owned payload has no Part
  wrapper, lowering may record
  `uk_effect_nonaddressable_schedule_part_insert_target_normalized` and target
  the replay-addressable schedule paragraph. Mismatched labels remain covered
  by the generic `instruction_text_reused_as_payload` blocker. Current witness:
  `asp/2002/11` affected by `ssi/2017/36 art. 21(3)`, `Sch. 2 Pt. 1 para. 17B
  and cross-heading`.
- Schedule paragraph insert metadata may name a compressed sibling range with
  plural cross-heading text, e.g. `Sch. 6 para. 45-48 and cross-headings`.
  The plural cross-heading suffix is not a body target; target splitting strips
  it only to expand the proved structural paragraph range. The extracted
  `BlockAmendment` must still carry one source-owned payload child per
  structural target, and each `P1group/Title` remains an owned heading carrier
  on its corresponding paragraph. Current witness: `ukpga/2020/17`, affected
  by `ukpga/2022/32 Sch. 17 para. 12(13)`.
- source text that says `for "X", wherever occurring, substitute "Y"` lowers
  under `uk_effect_wherever_occurring_substitution_text_patch`. This is a
  deterministic text-patch family, not manual compilation, because the source
  provides the exact old text, replacement text, and target row; when the effect
  feed has already split the row by target provision, each target receives the
  same all-occurrences patch. Lowering also emits a nonblocking
  `text_rewrite_lowering` observation for explicit all-occurrences rules so the
  executable rewrite is visible in reports, not only encoded in operation
  provenance.
- source text that says a quoted pair `A` and `B`, wherever those expressions
  occur, become respectively `C` and `D` lowers as two independent
  all-occurrences text patches under
  `uk_effect_respectively_all_occurrences_substitution_text_patch`. If an
  individual heading carrier lacks one of the paired preimages, replay records
  `uk_replay_heading_respectively_all_occurrences_absent_observed` instead of
  `uk_replay_heading_text_preimage_gap`; this nonblocking treatment is limited
  to the explicit respectively/all-occurrences rule, because the legal
  instruction is conditional on occurrence.
- the same respectively/all-occurrences family covers Scottish wording such as
  `for the words "A" and "B" wherever occurring there is substituted "C" and
  "D" respectively`. The feed target still supplies the replay address; a
  source header such as `In each of the following provisions...` does not let
  lowering invent additional targets beyond the effect-feed row.
- the same family also covers longer source-local series where the originals
  carry explicit all-occurrence markers, e.g. `for "A" (in each place), "B" (in
  each place) and "C" there is substituted "D", "E" and "F" respectively`.
  The parser pairs quoted originals and replacements by order only when the
  source clause itself says `wherever` or `in each/both places`; otherwise the
  unqualified series remains a blocked/manual text-patch frontier rather than
  being silently widened to all occurrences.
- source text that says `from "X" to the end substitute-` followed by an
  unquoted block lowers under
  `uk_effect_anchor_to_end_block_substitution_text_patch`. The rule is limited
  to unquoted block payloads so it does not duplicate existing quoted
  `TEXT_FROM_X_TO_END` substitution rules. The replay selector remains a
  bounded text-span operation; it may not become a structural replacement or
  consume sibling provisions.
- source text that targets `cross-heading` facets lowers only through explicit
  before-anchor whole-heading replacement, quoted text-patch, titled replacement
  split, or guarded heading-wrapper repeal lanes. Other crossheading rows are
  classified as `crossheading_target_unsupported` while UK lacks a safe facet
  replay lane for the claimed shape. These remain manual/future-compiler
  candidates, not section body replacements.
- alphabetic suffix labels such as `aa`, `ba`, or `ga` are part of the same
  local letter sequence as their base label. Replay insertion order must place
  `ga` after `g` and before later single-letter siblings such as `h` or `i`;
  it must not bucket every single-letter paragraph before all multi-letter
  labels, and a pure lettered paragraph set must not interpret `c` as Roman
  `100`. Current real witnesses: `asc/2021/1` / `wsi/2021/1349` `reg. 33`,
  inserting `s. 122(1)(ga)`, and `asc/2021/1` / `wsi/2022/797` `reg. 7(b)`,
  inserting `s. 159(4)(ba)`.
- whole inserted schedule payloads may arrive from amendment XML without
  descendant `eId` attributes. The owned phase is payload normalization:
  `uk_whole_schedule_payload_descendant_eid_synthesis` may assign descendants
  from the explicit schedule target root plus parsed source labels. Replay does
  not infer this identity later, and oracle alignment is not part of the rule.
  Repeated form labels that would duplicate an already synthesized local ID are
  left unaddressed and counted by the same observation; LawVM must not invent
  hidden suffixes to make a form-like payload satisfy tree uniqueness. Current
  real witnesses: `asc/2021/1` / `wsi/2022/797` `reg. 5`, inserting `Sch.
  10A`, and `asp/2000/5` / `asp/2003/9` `Sch. 13 para. 16`, inserting `Sch.
  5A-5C`.
- if such a whole-schedule form payload still violates the generic tree
  duplicate-label or label-order invariant after replay, classify it as
  `uk_replay_repeated_form_label_payload_shape_gap` rather than a generic
  payload-shape gap. This is an unresolved source-shape frontier: repeated form
  field labels are source text to preserve, but they are not yet a canonical
  legal-address lineage model.
- a text patch aimed at a missing descendant may recover to the immediate
  parent only when the parent exists, has no child carriers, and its own text
  contains the exact preimage
  - rule: `uk_replay_empty_descendant_parent_text_recovered`
  - family: `target_resolution_recovery`
  - strict disposition is `block`; quirks disposition is `apply`
  - current real witnesses: `asp/2000/11` / `asp/2012/8` `Sch. 7 para.
    15(8)(c)`, targeting `s. 16(1)(b)`, and `asp/2000/11` / `ukpga/2016/25`,
    targeting `s. 16(1)(a)`, where replay has a flat subsection parent carrying
    the exact preimage but no addressable paragraph child at that point in the
    amendment chain
- a source-carried word-tail substitution may materialize explicit child
  carriers under a flat parent when the source says `for the words following
  "anchor" substitute` and the extracted payload supplies the child provisions
  - rule: `uk_replay_source_carried_structured_tail_substitution_recovered`
  - family: `source_carried_structured_tail_substitution`
  - replay trims only the parent text after the quoted anchor, then inserts
    only the claimed child payloads; it must not infer unrelated siblings
  - strict disposition is `block`; quirks disposition is `apply`
  - current witness: `ukpga/2020/17` `Sch. 20 para. 5(a)(b)`, affected by
    `ukpga/2020/17 Sch. 22 para. 81(5)`, where enacted paragraph 5 is a flat
    `where ...` sentence and the current oracle has structural items `(a)` and
    `(b)`

Current subordinate-sibling payload invariant:

- when a selected replacement payload is a `P3` inside a `BlockAmendment`,
  immediately following sibling `P4` rows can belong to that payload even if
  the XML does not nest them directly
  - current example: `ukpga/2001/11` `s. 7(9)(b)`
  - the extracted block contains:
    - `P3(b)`
    - then sibling `P4(i)`, `P4(ii)`, `P4(iii)`
  - compiling only the bare `P3` head loses the inserted subparagraph subtree
    and leaves a fake oracle-only `section-7-9-b-i/ii/iii` tail
  - payload selection should therefore attach direct trailing subordinate
    siblings when the selected amendment node is a direct child of
    `BlockAmendment` / `InlineAmendment`

Current nested-roman target invariant:

- fixed-prefix sibling expansion must not peel a roman subitem off a lettered
  parent label
  - current example: `ukpga/2001/11` `s. 8(2)(b)(i)`
  - this is nested `paragraph b / subparagraph i`, not sibling targets
    `paragraph b` and `paragraph i`
  - a narrow safe rule is:
    - if the would-be sibling family starts with a lettered item label and the
      later groups are roman subitem labels (`i`, `ii`, `iii`, ...), keep the
      lettered prefix fixed and do not split there
- insert target lookup must not use recursive descendant fallback to prove an
  existing-target conflict
  - current witness: `ukpga/2020/17` `Sch. 26 para. 12(2)`, affected by
    `uksi/2020/1520 reg. 6(2)(c)`
  - `schedule:26/paragraph:12/subparagraph:2` must not bind to nested item
    `(ii)` inside `subparagraph:1/item:c`; if no direct subparagraph `(2)`
    exists, replay may insert the claimed sibling under paragraph 12
  - this preserves exact eId and direct-path matching while preventing a
    target-hijack by recursive roman/numeric equivalence
- parenthesized same-prefix alphabetic ranges must expand before target
  parsing, rather than letting the dash become a synthetic legal label
  - examples: `(da)-(dc)` becomes `(da)`, `(db)`, `(dc)`; `(axa)-(axc)`
    becomes `(axa)`, `(axb)`, `(axc)`
  - current witnesses: `ukpga/2020/17` `Sch. 26 para. 12(1)(da)-(dc)` by
    `uksi/2020/1520 reg. 6(2)(b)` and `Sch. 18 para. 38(axa)-(axc)` by
    `ukpga/2023/50 Sch. 14 para. 20`
  - broad base-26 ranges such as `(az)-(bc)` remain unsupported unless owned
    separately; no public `item:-` address should be emitted
- parenthesized numeric-to-alphanumeric ranges must mirror ordinary section
  range expansion: `s. 2(11)-(12B)` becomes `s. 2(11)`, `s. 2(12)`,
  `s. 2(12A)`, and `s. 2(12B)`, rather than leaking the dash into a synthetic
  descendant label
  - current witness: `asp/2001/8` / `asp/2007/4 s. 7`, where the affected
    metadata `s. 2(11)-(12B)` previously lowered one malformed target
    `section:2/subsection:11/paragraph:-/subparagraph:12b`
- one-letter to same-stem two-letter parenthesized ranges are supported as a
  narrow sibling family: `s. 26D(4)(b)-(bb)` becomes `(b)`, `(ba)`, `(bb)`.
  This does not generalize to broad base-26 ranges.
  - current witness: `asp/2000/1` / `ukpga/2014/2 Sch. 12 para. 47(2)`,
    where the affected metadata previously lowered one malformed target
    `section:26d/subsection:4/paragraph:b/subparagraph:-/item:bb`
- canonical roman parenthesized ranges take precedence over same-stem
  alphabetic expansion. `s. 25(3)(a)(i)-(iv)` becomes `(i)`, `(ii)`,
  `(iii)`, `(iv)`, not the alphabetic stem family `(i)`, `(ia)`, ... `(iv)`.
  - current witness: `asp/2001/8` / `ssi/2009/131 art. 4`, where affected
    metadata `s. 25(3)(a)(i)-(iv)` previously produced bogus subparagraph
    labels such as `ia`, `ib`, and `ic`
- adjacent roman metadata suffixes may form a same-depth sibling family once a
  fixed parent context has already been established
  - example: `s. 5(12)(a)(iii)(iv)` becomes `s. 5(12)(a)(iii)` and
    `s. 5(12)(a)(iv)`, not a nested `item iv` under subparagraph `iii`
  - current witness: `asp/2002/1` / `asp/2025/4 s. 39(2)(h)(iii)`, where the
    effects feed compacted two source-owned inserted `P4` sibling payloads into
    one affected-provisions string
  - the existing nested guard remains binding: `letter + roman` shapes such as
    `(b)(ii)` or `(a)(zi)` stay nested unless a separate source-owned rule
    proves sibling scope
- body target identity must preserve every descendant suffix after the section
  root when deriving fallback/oracle EIDs
  - example: `section:5/subsection:12/paragraph:a/subparagraph:iii` derives
    `section-5-12-a-iii`, not `section-5-12-a`
  - this matters for chained same-source inserts because later sibling anchors
    must point at the prior generated target, not at the shared parent
- tight abbreviation+label forms such as `para.032B` are normalized before
  target parsing. The missing space is a metadata transport defect, and leading
  numeric zero padding is stripped from provision labels.
  - current witness: `asp/2002/11` / `ssi/2013/197 Sch. 2 para. 9(b)`, where
    `Sch. 2 para.032B` previously lowered `paragraph:paragraph032b`
- adjacent same-length multi-letter metadata suffixes that denote source
  sibling insertions must split as siblings, not nested descendants
  - examples: `Sch. 26 para. 14(aa)(bb)` becomes `Sch. 26 para. 14(aa)` and
    `Sch. 26 para. 14(bb)`; `Sch. 27 para. 15(2)(za)(zb)` becomes sibling
    items under `15(2)`
  - current witnesses: `ukpga/2020/17` `Sch. 26 para. 14(aa)(bb)` by
    `uksi/2020/1520 reg. 6(3)`, `Sch. 27 para. 15(2)(za)(zb)` by
    `ukpga/2021/11 Sch. 13 para. 26(28)`, and
    `Sch. 21 para. 2(2)(ca)(cb)` by `ukpga/2026/2 s. 11`
  - the existing negative cases remain binding: `letter + roman` and unrelated
    letter/suffix shapes such as `(b)(ii)` or `(a)(zi)` stay nested

Current `ukpga/2001/11` near-solved interpretation:

- after subordinate-sibling payload attachment and nested-roman target
  preservation, `ukpga/2001/11` moves again from `98.1%` to `99.2%`
- the `s. 7(9)(b)` subtree now carries its `i/ii/iii` children
- `s. 8(2)(b)(i)` now targets the real nested subparagraph
- the remaining raw tail is only:
  - replay-only `section-10-3-bc`
  - oracle-only `section-10-3-cn1`
- row-level inspection already types `s. 10(3)(bc)` as
  `collapsed_subtree_oracle_shape`, so `2001/11` should now be treated as
  effectively off the live replay-semantic frontier unless a fresh backing row
  appears

Current missing-live-branch oracle invariant:

- a text-level amendment cannot itself explain a whole live branch disappearing
  from oracle
  - current example: `ukpga/2009/24` `Schedule 5`
  - six applied `words substituted` rows from `uksi/2012/2007 Sch. para. 100`
    target existing base nodes like:
    - `schedule-5-paragraph-3-2-a`
    - `schedule-5-paragraph-5-3`
  - base/replay expose the whole `schedule-5*` branch
  - oracle exposes no `schedule-5*` EIDs at all
  - there is no corresponding `Schedule 5` repeal/omission row backing that
    disappearance
- when:
  - the op family is text-only (`text_replace` / `text_repeal`)
  - the target exists in base
  - the target and its parent chain are missing from oracle
  - the row itself does not describe a structural removal
  then this is compare-side `oracle_missing_live_branch`, not a replay bug
- after typing that class, `ukpga/2009/24` leaves the active replay frontier
  even though the raw replay residual still contains `schedule-5`

Current bench replay-regime invariant:

- UK bench replay rows must disclose and persist the replay regime that produced
  each score:
  - metadata backfill enabled/disabled
  - oracle EID alignment enabled/disabled
  - applicability mode
  - authority mode
  - authority rejection count
- `--no-oracle-alignment` disables both:
  - replay-time oracle inputs in `replay_uk_ops`
  - post-replay `align_uk_replay_to_oracle_with_report`
- `--source-first-candidate` is a named candidate regime, not a hidden
  benchmark tweak:
  - metadata backfill disabled
  - oracle alignment disabled
  - applicability remains feed-applied aware
  - authority mode is `source_text_only`
- source-first conflicts with explicit opposite flags must fail at CLI argument
  normalization time, rather than producing a mixed ambiguous score.
- `uk-bench` and `uk-replay` share the same replay-regime normalization so a
  one-statute JSON replay can be compared directly with a corpus benchmark row.
  The replay JSON payload carries the normalized regime and compile rejection
  counts, including source-text authority rejections in source-first mode.
- `uk-replay` text output must expose replay adjudication totals and kind
  counts when replay skipped/no-oped operations. JSON-only visibility is not
  sufficient for interactive diagnosis because unsupported replay actions are
  part of the coverage surface.
- `UKReplayPipeline.apply_ops` must invoke replay-time oracle EID grounding
  when oracle alignment is enabled and an oracle map is available. Otherwise
  `uk-replay` advertises the oracle adapter but compares ungrounded replay
  IDs, which turns bounded oracle identity drift into false replay residuals.
- Affecting-provision refs with parenthesized child ranges such as
  `art. 2(4)(c)-(g)` must not be widened to the whole parent. If the child
  endpoints are addressable in the affecting XML, extraction may return a
  synthetic bounded source wrapper containing only the named children and must
  emit `uk_affecting_act_parenthesized_range_source_extracted`.
- Affecting-provision refs may contain an inserted first-subparagraph context
  that is not present in the affecting source XML. Example: effects metadata
  may cite `Sch. 22 para. 88(1)(a)` after paragraph 88 was made into
  sub-paragraph `(1)`, while the source XML still exposes the lettered child as
  `schedule-22-paragraph-88-a`. Exact source lookup must be attempted first.
  Only after it misses may extraction normalize this schedule source ref to
  `Sch. 22 para. 88(a)` under
  `uk_affecting_act_implicit_first_subparagraph_context_ignored`, recording the
  original ref, normalized ref, authority layer, and extracted element id.
- UK bench rows must also persist replay lowering rejection totals, including
  the blocking subset. A replay score without its unsupported/no-op lowering
  surface is not a coverage metric; it hides which source effects were parsed
  but not executable.
- UK bench rows must preserve lowering rejection rule counts, including the
  blocking subset. Totals alone are not enough for a saved run because
  `payload_missing`, `no_ops`, and nonstructural unsupported families imply
  different next actions.
- Human-readable UK bench reports must print those lowering rejection families
  when present; saved CSV visibility alone is not enough during interactive
  frontier triage.
- UK bench rows must preserve effect-feed rejection rule counts, not only total
  counts. A saved benchmark is an evidence artifact; dropping rule IDs makes
  source acquisition/parse failures indistinguishable after the run.
- UK bench rows must preserve authority rejection rule counts, not only total
  counts. Source-text authority filtering is a compile-time evidence lane; a
  saved replay benchmark must retain which authority rule rejected each effect
  family.
- UK bench replay rows must preserve replay adjudication totals and kind
  counts. Unsupported actions, missing targets, and replay-time no-op/skip
  findings are part of the replay coverage surface; a benchmark score without
  those counts can hide non-applied operations.
- UK bench rows must preserve oracle-alignment method and node-safety
  provenance: match-method counts, transparent wrapper clears, before/after
  node counts, and node-count mismatch. Count-only alignment reporting hides
  whether benchmark improvement came from safe identifier grounding or a
  structurally suspect adapter pass.
- Candidate/residual triage from a saved UK bench run must replay residuals
  under the replay regime persisted on each bench row. Using default replay
  settings during `uk-candidates` would classify a source-first benchmark
  frontier under a different authority lane.
- `uk-candidates` must also inspect replay-applicable effects and summarize
  effect rows under the saved bench row's applicability mode. It is not enough
  for only the residual replay step to use the saved regime; otherwise the
  candidate inventory and the replay residuals are produced under different
  semantic lenses.
- `uk-replay --json` must not emit placeholder oracle-alignment counts. It is
  a replay-executor-inputs lane, not the post-replay bench/evidence adapter
  lane, but the executor's alignment events are still evidence and must be
  surfaced as event counts, match-method counts, and typed unavailable reasons
  when the lane is disabled.
- `uk-replay --json` must include bounded oracle-alignment event samples in
  addition to aggregate counts. Samples are diagnostic evidence, not a replay
  authority surface; they let a residual reviewer see which adapter match keys
  fired without rerunning a one-off debugger.
- `uk-replay` text mode must print the same high-level evidence lanes as JSON:
  source status/size, replay regime, compile rejection lane counts,
  compile rejection rule counts by lane, oracle-alignment availability/counts,
  executor-input match-method counts, and normalized EID compare counts when an
  oracle comparison exists. Terminal output is often copied into notes, so it
  cannot hide these lanes behind `--json`.
- `uk-replay --json` must report enacted/oracle source status with the same
  `absent` / `too_small` / `available` vocabulary used by `uk-effect`,
  `uk-effects`, and `uk-eids`. Boolean oracle availability is not enough:
  missing source and suspicious cached source imply different acquisition
  failures, and too-small oracle blobs must not be parsed as oracle witnesses.
  JSON also carries `*_source_sha256` so a replay report identifies the exact
  archived source bytes behind each witness.
- Human-readable `uk-replay` output must include the enacted/oracle source URLs
  and SHA-256 identities alongside source status and byte size. Interactive
  replay triage should not require switching to JSON to identify the compared
  source surfaces.
- When `uk-replay --fetch-missing` is used, human-readable output must also
  include prefetch event counts and rule counts, including blocking-only rule
  counts. Acquisition repair evidence must not exist only in stderr or JSON.
- UK evidence bundles must use that same source-status vocabulary for enacted
  and oracle source surfaces. A proof bundle must not parse a too-small cached
  XML blob or collapse it into ordinary absence.
- UK evidence bundles must also carry enacted/oracle source SHA-256 hashes when
  archive bytes exist, including too-small and parse-rejected blobs. Proof
  bundles are source witnesses, not only derived legal-state reports.
- UK evidence bundles that stop early on unavailable source must still emit the
  benchmark comparison class (`no_enacted_eids` / `no_oracle_eids`) with
  `core_comparison=false`; the EID/effect counts stay unknown because the source
  was deliberately not parsed.
- Those early UK evidence bundles must also carry the shared UK replay regime,
  applicability regime, source-availability summary, and empty compile
  observation/rejection summaries. A source-unavailable row is a typed evidence
  row, not a reduced stderr-only failure.
- The evidence CLI error path for those unavailable UK source bundles must print
  enacted/oracle source status, byte size, URLs, hashes when present, and
  comparison class before exiting. `ERROR: NO_ORACLE` alone is not enough
  acquisition evidence.
- With `--json`, that same evidence CLI error path must emit the typed bundle to
  stdout before exiting non-zero. Source-unavailable failures are still evidence
  artifacts; JSON callers must not be forced to scrape stderr.
- UK evidence text rendering must distinguish feed observations, blocking
  feed-parse/acquisition rejections, lowering observations, and blocking
  lowering rejections. A generic `feed rules` line is not enough when JSON has
  a dedicated `blocking_effect_feed_parse_rejection_rule_counts` lane.
- UK evidence bundles must route the initial effect-count load through the same
  feed observation/rejection lane as replay compilation. If that preliminary
  load fails, the bundle records `uk_effect_feed_count_error` with exception
  detail, `blocking=true`, `strict_disposition=block`, and
  `quirks_disposition=record`; the proof bundle must remain a typed evidence
  artifact rather than becoming a generic Python exception.
- UK evidence-review summaries must count UK comparison classes and core/non-core
  comparison status alongside source status. Review output is a benchmark
  evidence surface, not only a proof-tier inventory.
- UK evidence-review top rows must include row-level enacted/oracle source
  status, byte size, URLs, SHA-256 hashes, comparison class, and core/non-core
  status. A copied review row should identify the exact source surfaces and
  comparison stratum without reopening the JSON bundle.
- Chunked/live UK evidence-review merging must preserve the same UK comparison
  class and core/non-core count fields as single-bundle review. Review parallelism
  must not change the evidence surface.
- UK evidence-review rows must distinguish review input/materialization lanes
  from legal proof lanes. Artifact review rows are `artifact_bundle`; live
  review rows identify `live_statute_id` or `live_oracle_corpus` plus whether
  the reviewed bundle came from a cache hit, cache miss, or uncached live build.
  These fields explain the review surface only; they must not alter proof
  claims, replay adjudications, or source authority classification.
- Evidence-review merge fields must stay aligned with `_review_bundles` count
  outputs. Display-tier and sparse-blocker counts are also merge-sensitive
  evidence lanes, not purely local rendering details. A regression test should
  fail whenever a new `by_*` review count output is not merge-registered.
- The UK source-status vocabulary and byte threshold are frontend-owned in
  `lawvm.uk_legislation.source_state`; diagnostic tools may expose legacy string
  tuples, but they must not define their own source-size threshold locally.
- UK bench rows must persist enacted/oracle source status and source byte size
  with that same vocabulary. A saved benchmark row that only says `NO_ORACLE`
  loses whether the acquisition problem was absent source or a cached
  too-small/invalid-looking source body.
- `uk-bench` must classify both enacted and oracle source states before
  returning `NO_ENACTED` or `NO_ORACLE`. Early failure on one side must not hide
  the acquisition state of the other comparison surface.
- UK bench corpus CSV generation must record enacted/oracle source status and
  byte size, not only locator presence. Locator coverage and usable XML coverage
  are distinct acquisition facts.
- `uk-bench --compare` must print enacted/oracle source-status counts for both
  runs. Score movement is not interpretable until acquisition-state changes are
  visible.
- `uk-bench --compare` must also print row-status, comparison-class, and
  core-benchmark distributions for both runs. A score delta is not sufficient
  evidence if rows moved from core comparison to source/oracle pathology classes
  or from OK into acquisition/replay error states.
- Loading legacy UK bench CSVs must derive `core_benchmark` from
  `comparison_class` when the explicit column is absent. Treating every legacy
  classed row as core hides source/oracle pathology rows in compare and
  candidate-triage summaries.
- UK comparison classification treats exact replay-vs-oracle structural
  equality as `commensurable` before applying enacted-vs-oracle expansion
  labels. An oracle may legitimately be larger than the enacted source because
  effects were applied; once replay reaches the same EID set, calling the row
  `unapplied_oracle_expansion` is stale triage evidence.
- `uk-bench --compare` must also print replay-regime distributions,
  source-parse observation/rejection totals/rules, effect-feed rejection
  totals/rules, authority/lowering rejection deltas, replay adjudication deltas,
  and oracle-alignment count/method deltas. Benchmark comparison is an evidence
  comparison, not only a score comparison.
- `uk-candidates` rows must preserve those saved bench source-status fields.
  Candidate triage may re-run archive-backed residual analysis, but the saved
  benchmark source state remains part of the evidence surface and should survive
  copy/paste or JSON export.
- `uk-candidates` rows and summaries must also preserve saved bench authority
  rejection totals/rules. Candidate triage may inspect effects again, but it
  must not drop the compile-time authority lane that produced the benchmark
  frontier.
- `uk-candidates` residual replay analysis must collect and preserve compile
  rejection totals/rules from the residual replay compile pass. It is not enough
  to show effect-inventory rejections when the residual replay itself may have
  skipped feed-parse, lowering, or authority-filter lanes under the saved
  benchmark regime.
- `uk-candidates` text summaries must print inspected/available effect counts
  plus source/compare evidence families and candidate/non-candidate splits.
  JSON-only visibility hides whether frontier rows are real replay candidates,
  source-pathology buckets, or compare-shape artifacts.
- `uk-candidates --fast` summaries must explicitly count candidate-analysis
  skipped rows. Status text alone is not enough because zero candidate/rejection
  counts in fast mode mean "not inspected", not "clean".
- Human-readable `uk-candidates` row output must print the saved enacted/oracle
  source status, byte size, URL, and SHA-256 hash, not only aggregate summary
  counts. A single copied candidate row should preserve whether its source
  surfaces were available, absent, or too-small and which archive bytes were
  used.
- `uk-candidates` aggregate JSON/text summaries must count saved enacted/oracle
  source statuses. Row-level preservation alone is insufficient for dashboard
  triage because source-unavailable frontier shape should be visible without
  scanning every row.
- `uk-candidates` rows and aggregate summaries must disclose the saved UK replay
  regime (`metadata_backfill`, `oracle_alignment`, `applicability_mode`, and
  `authority_mode`). Candidate/residual triage uses these settings for replay;
  omitting them from JSON/text output makes copied evidence ambiguous.
- `uk-candidates` summary objects must duplicate configured diagnostic budgets
  (`top`, `score_mode`, `effect_budget`, and `residual_budget`) from the filters
  block. Summary-only consumers should not need to read two JSON branches to
  understand whether truncation/skips came from the configured diagnostic
  budget.
- `uk-effect` and `uk-effects` must make the replay applicability lens explicit
  in JSON and text reports. Their default remains
  `effective_date_plus_feed_applied`, but `effective_date_only` inspection must
  not silently report under a stricter policy than the saved benchmark or replay
  regime being investigated.
  - `uk-candidates --fast --residual-only` must keep source-unavailable residual
    analysis rows visible. Missing enacted/oracle comparison surfaces are not
    clean residual sets; they are triage rows with
    `uk_residual_analysis_source_unavailable`.
  - residual-analysis compile/apply exceptions in `uk-candidates` are row-local
    execution failures, not command-level aborts. The row status is
    `residual comparison execution unavailable`, the triage rule is
    `uk_residual_analysis_execution_unavailable`, and the execution lane carries
    `uk_residual_compile_exception_recorded` or
    `uk_residual_apply_exception_recorded` with exception type/message and
    `blocking=true`.
- `evidence`, `prove-oracle`, and live `evidence-review` also use that shared
  normalization for UK runs; otherwise proof bundles and benchmark/replay rows
  could disagree about what `--source-first-candidate` means.
- `--allow-metadata-only-effects` / `--no-metadata-only-effects` is wired into
  the shared UK replay regime. When metadata-only effects are excluded, the
  compiler must emit `uk_effect_metadata_only_selection_rejected` rows instead
  of silently filtering those source-lane effects.
- UK replay-regime argparse wiring is also shared. Adding a new UK replay
  regime flag in one diagnostic entrypoint but not another creates hidden
  benchmark/proof drift, so the parser definition belongs beside the shared
  normalization in `uk_replay_regime.py`.
- The CLI jurisdiction flag must survive both positions, `lawvm -j uk <cmd>` and
  `lawvm <cmd> -j uk`; subcommand parsers must not overwrite a global UK
  selection with their own default when UK replay-regime flags are present.
- Human-readable `evidence-review` output must print the UK replay regime when
  present; JSON-only visibility is not enough for interactive benchmark/proof
  triage.
- `evidence-review` summaries must also aggregate enacted/oracle source-status
  counts. Otherwise `NO_ORACLE` and weak proof batches hide whether the blocker
  was absent source, too-small cached XML, or a replay/proof limitation.
- The generic `lawvm replay -j uk` entrypoint is a UK frontend surface too. It
  must expose the same replay-regime flags as `uk-replay` and map its generic
  `--archive` argument to the UK farchive `db` input before dispatch.
- UK replay-regime flags exposed on shared entrypoints must reject when the
  selected jurisdiction is not UK. Silently ignoring `--source-first-candidate`
  or `--no-oracle-alignment` on a non-UK run creates false evidence about the
  regime that produced the output. The same rejection rule applies to adjacent
  UK diagnostic flags such as `--no-metadata-only-effects`.
- `uk-bench --show` must print source-parse, bench-exception, effect-feed,
  authority, lowering, and replay-adjudication evidence over all attempted rows
  before the no-valid-results return. `ERR`, `NO_ORACLE`, and `NO_ENACTED` rows
  are evidence lanes, not invisible non-results.
- When those all-attempted-row replay evidence totals differ from the ordinary
  replay-scored block, text output must label them as `All-row ...` so copied
  reports do not confuse failed-row evidence with the replay-scored subset.
- `uk-bench` history `replay_regimes` must count all attempted rows, not only
  `OK` rows. The replay regime is run-configuration evidence even when the row
  failed before producing a scored replay.
- `lawvm bench -j uk --no-save` is the clean smoke-test path for benchmark
  coverage checks. It may print the full report, but it must not write run CSV,
  score-witness sidecar, or benchmark-history artifacts.
- Full-corpus UK replay sweeps must be memory-aware. The implicit UK replay
  worker default should remain conservative for WSL2-like environments; callers
  may pass `--parallel N` when they explicitly want more throughput.
  `--worker-max-tasks N` is an operational guard that recycles parallel workers
  after N statutes to bound long-run worker RSS growth.
  Benchmark aggregation must not retain full diagnostic tuples for every
  report sample; full evidence belongs in streamed CSV/JSONL sidecars, while
  in-memory top-N report rows retain only scalar scores, counts, status,
  source identity, and timing/RSS fields.
  Saved UK bench history rows include the maximum observed `process_maxrss_kb`
  and the statute row after which it was observed, so history review can track
  memory pressure without reopening every row CSV.
  Saved-run display is part of the same operational contract: `uk-bench --show`
  and `uk-bench --compare` should read CSV summary fields by default and avoid
  eager diagnostics JSONL loading. `--show` may load diagnostics only when the
  caller asks for replay-adjudication samples.
  `uk-bench --summary-only` is a terminal-output budget, not an evidence
  filter. Saved CSV, score-witness, diagnostics, and history artifacts remain
  unchanged; only the printed report is restricted to headline scores,
  aggregate evidence counts, and peak RSS. For `--compare`, summary-only
  similarly suppresses full rule-count dictionaries and top-row lists while
  preserving headline score and aggregate evidence-count deltas.
  `uk-bench --compare` uses replay-primary row scores when replay columns are
  present (`replay_commencement_score`, then `replay_score`) and falls back to
  commencement/raw scores for older non-replay saved runs. Saved history
  `avg_score` remains the original benchmark primary lane and must not be
  relabelled as replay-primary unless that saved aggregate changes too. A
  saved run that mixes replay-primary rows with fallback raw/commencement rows
  is invalid compare evidence; `uk-bench --compare` must reject it instead of
  averaging different score lanes.
  `uk-candidates --summary-count-limit N` follows the same reporting-budget
  rule for JSON summaries only: count maps may be truncated for readability
  only when the omission counts are explicit, and the underlying saved-run
  evidence remains unchanged. `uk-candidates --row-count-limit N` is the
  corresponding emitted-row JSON budget: it limits per-row count maps, records
  `row_count_map_omissions`, and must not change row selection, frontier
  matching, summary aggregation, or evidence exports.
- UK replay preparation is a core-boundary normalization point. Source/local
  address kind aliases that are not core `IRNodeKind` values must not leak into
  accepted replay operations. Currently `point` is canonicalized to the core
  `item` kind and stamped with `uk_address_alias:point_to_item`; this is an
  address-kind alias normalization, not target widening and not an action-family
  mutation.
- `lawvm bench -j uk --curate-preset
  canary|tight|stress|modern-canary|modern-tight|hard-canary|hard-tight|hard-stress` writes a
  source-complete curated corpus without requiring the caller to remember
  standard row budgets or output paths. Preset defaults are canary=40,
  tight=200, stress=400, modern-canary=40, modern-tight=200, hard-canary=40,
  hard-tight=200, and hard-stress=400. The modern presets default to
  `--min-year 1990` unless an explicit year filter is supplied. The hard
  presets remain source-complete, exclude zero-effect rows, and prefer heavier
  effect/source rows within each stratum so they are suitable for replay
  benchmax loops rather than representativeness claims. Without
  `--curate-corpus`, canary writes
  `data/uk/bench_corpus_smoke.csv`, tight writes
  `data/uk/bench_corpus_tight.csv`, modern-canary writes
  `data/uk/bench_corpus_modern_smoke.csv`, modern-tight writes
  `data/uk/bench_corpus_modern_tight.csv`, stress writes
  `data/uk/bench_corpus_stress.csv`, hard-canary writes
  `data/uk/bench_corpus_hard_smoke.csv`, hard-tight writes
  `data/uk/bench_corpus_hard_tight.csv`, and hard-stress writes
  `data/uk/bench_corpus_hard_stress.csv`. `--curate-size` may still override
  the preset size.
- `lawvm bench-regression-guard -j uk --baseline <old> --current <new>` reads
  saved UK bench runs from `data/uk_bench_runs/<label>.csv`. It compares the
  row-wise replay-primary score when replay columns are present
  (`replay_commencement_score`, then `replay_score`) and falls back to `score`
  only for non-replay saved runs; it must fail rather than compare different
  score lanes. In a replay-column CSV, any row with a fallback `score` or
  `similarity` value but no replay-primary value is mixed-lane guard evidence
  and invalid; wholly unscored/error rows may still remain outside the common
  scored comparison. When a UK saved run includes replay-regime columns, every
  common scored row in that run must have complete regime evidence; partial
  blank-cell regime evidence is invalid because regime mismatch checks would
  otherwise skip that row. When both UK saved runs include complete
  replay-regime evidence, the guard must also fail rather than compare common
  scored rows produced under different replay regimes. UK saved-run labels are
  direct filenames, not the
  timestamp-suffixed Finland convention.
  `--max-duration-regressions N` enables an explicit `duration_s` guard for
  saved runs; this is opt-in because timing evidence is environment-sensitive,
  but when enabled missing `duration_s` columns are treated as invalid benchmark
  evidence. A comparison with zero common scored rows, or an enabled duration
  guard with zero common `duration_s` rows, must fail rather than report a green
  empty aggregate. `--max-phase-regressions N` enables the same opt-in guard for
  saved `phase_*_s` timing cells, excluding `phase_total_s`; it fails if there
  are no common phase rows or no comparable non-total phase cells, and reports
  the slowest row/phase regressions separately from score regressions. Repeated
  `--phase NAME` arguments restrict the phase guard to named phases such as
  `compile_ops` and `replay`; a selected phase with no comparable timing cells
  in both saved runs is invalid guard evidence. `--max-rss-regressions N`
  enables the same opt-in pattern for saved `process_maxrss_kb` cells, using
  `--rss-threshold-mb` as the run-peak memory-growth threshold. It must not
  compare `process_maxrss_kb` as if it were per-statute allocation evidence:
  it is process high-water RSS, and in parallel/recycled runs a row can inherit
  a previous row's peak. The RSS guard therefore compares measured run peaks
  and must fail if either run has no positive measured RSS rows instead of
  reporting a green empty aggregate over missing or platform-unsupported zero
  values.
- `lawvm bench -j uk --statute <ID>` must filter the UK bench corpus to exactly
  the requested statute before applying diagnostic limits. If the statute is not
  present in the corpus, the command fails visibly rather than saving an empty
  run.
- UK bench filters that leave no rows must fail before running or saving,
  except for the explicit `--limit 0` diagnostic budget. Empty saved runs from
  accidental year/type/statute filters are false benchmark evidence.
- `lawvm bench -j uk --parallel N` requires `N >= 1`. Nonpositive worker counts
  are invalid command input, not a hidden request for sequential fallback.
- `lawvm bench -j uk --min-year A --max-year B` rejects `A > B` before archive
  access. Inverted year ranges are invalid command input, not an empty benchmark
  corpus.
- UK bench parallel execution failures are row evidence. Worker archive-open
  failures and parent-side future failures must produce `ERR` rows with
  `uk_bench_unclassified_exception` observations instead of aborting the whole
  benchmark.
- UK bench submit-time failures and sequential scorer failures follow the same
  rule: batch isolation is allowed only when the failed statute is represented
  as a typed `ERR` row with replay regime and source-state context preserved.
- `uk-replay` must surface replay-time effect source diagnostics with the same
  lane vocabulary as `uk-bench`: nonblocking `effect_source_pathology`
  observations such as `uk_effect_source_pathology_classified` stay separate
  from `source_acquisition` observations such as cached, missing, too-small, or
  parse-failed affecting-act XML records. Blocking source-acquisition rejection
  counts are derived from that observation lane with the shared compile-record
  classifier, not by filtering the observation lane out first. JSON payloads
  and text summaries must preserve both lanes so interactive replay can explain
  why a replay had no operations instead of relying on batch-only benchmark
  reports.
- `uk-replay` must also preserve manual compile frontier observations as their
  own `manual_compile_frontier` compile lane in JSON payloads and text
  summaries. Manual-frontier rows are evidence about deterministic replay
  limits; interactive replay must not drop them while bench/candidates/evidence
  bundles preserve them. The replay payload/text surface must include both the
  generic diagnostic `rule_id` count and the manual frontier status/rule-id
  counts, so copied output distinguishes deterministic rows from manual-candidate
  and source-insufficient rows.
- A manual-frontier row with `strict_disposition=record` is an observation even
  if it omits the legacy `blocking=false` flag. Replay JSON/text may still
  treat rows with neither `blocking` nor `strict_disposition=record` as blocking
  for legacy safety, but the manual compile frontier must not become a compiler
  failure by serializer accident.
- `uk_manual_frontier_unclassified` is an unresolved evidence bucket, not a
  disposable default. Source adjudication, bench save/load, diagnostics sidecars,
  and `uk-candidates` JSON/text must preserve its status and rule count so new
  UK blocker families remain visible until deliberately classified.
- `uk-bench` may refine manual-frontier rows after replay when replay emits
  exact preimage-gap adjudications for the same effect/op ID. This does not
  change lowering or replay. It corrects evidence summaries that were compiled
  before live replay state was available; affected rows become
  `uk_manual_frontier_text_patch_preimage_chain_gap` / `source_insufficient`.
  The current exact replay kinds are heading text, insert-anchor, monetary
  amount, parenthetical omission, and same-target text-patch preimage drift.
- `uk-candidates` residual replay analysis is also a compiler surface. Its
  `residual_compile_observations` and `residual_compile_rejections` must carry
  the same `effect_source_pathology` and `source_acquisition` lanes as
  `uk-replay`, not just feed/lowering/authority lanes, because residual triage
  often recompiles under a saved replay regime.
- `uk-candidates` saved-bench row evidence must preserve manual compile
  frontier counts from `uk-bench` (`manual_compile_status_counts` and
  `manual_compile_rule_counts`) in the same row-level rejection/evidence line as
  source-pathology and lowering counts. Otherwise a fast candidate report can
  hide whether residual work is deterministic parser work, manual work, or
  source-insufficient.
- Archive-backed `uk-candidates --manual-compile-evidence-jsonl PATH` writes
  all inspected `manual_compile_candidate` effect rows as
  `lawvm.uk_manual_compile_frontier.v1` work items. It must reject `--fast`
  because saved-bench counts are not enough source witness for manual work, and
  it must still write an empty JSONL plus JSON/text metadata when the selected
  frontier has zero rows. Each row must preserve the replay/authority regime
  used for classification, so a copied work queue does not lose whether it came
  from source-first, current-mixed, or a narrower applicability lens.
- `uk-candidates --manual-compile-evidence-status STATUS` is a repeatable,
  explicit exporter filter for that same source-witnessed work queue. The
  default remains `manual_compile_candidate`, but deterministic frontend
  candidates such as `deterministic_frontend_candidate` may be exported when
  the caller intentionally wants parser/lowering work items rather than human
  semantic compilation. `actionable` and `all_actionable` expand to both
  actionable statuses. The JSON/text metadata must disclose the status filter
  used so mixed work queues are not mistaken for manual-only review queues.
- Saved-bench replay adjudications are a review frontier too.
  `uk-candidates --replay-adjudication-evidence-jsonl PATH` writes the selected
  frontier's saved replay adjudications as `lawvm.uk_replay_adjudication_frontier.v1`
  work items, filtered by `--replay-adjudication-kind` when supplied. This is a
  triage/export surface only: it carries the saved replay regime, source
  witnesses, adjudication bucket, detail payload, blocking/strict dispositions,
  and stable work-item IDs, but it does not authorize replay repair or compile
  manual claims. The filter accepts residual-claim aliases where useful; for
  example `uk_text_match_already_rewritten_mixed_residual_eids` expands to
  `uk_replay_text_match_already_rewritten` so review exports can start from the
  bench residual claim kind and still sample the underlying replay witness.
- UK evidence/proof bundles are also replay evidence surfaces. Their
  `compiler_observations.uk_compile_observation_summary` and
  `uk_compile_rejection_summary` must preserve effect source pathology and
  source acquisition lanes with the same blocking split used by `uk-replay`,
  so a copied proof bundle does not lose why source-first replay produced few
  or no executable operations. The blocking classifier is the same as replay:
  explicit `blocking` wins, `strict_disposition=record` is nonblocking, and
  rows with neither marker remain blocking for legacy safety.
- Nonblocking source-acquisition observations are valid evidence. For example,
  `uk_affecting_act_xml_cached_recorded` records acquisition state without
  raising a compile rejection. Bench diagnostics, replay payloads, candidate
  residual payloads, single-effect reports, text inspection reports, and proof
  bundles must keep such rows in the `source_acquisition` observation lane
  while excluding them from blocking source-acquisition rejection counts.
  `uk-bench` saved rows and history output must also persist source-acquisition
  observation counts/rules, not just blocking acquisition rejection counts.
  Human-readable `uk-effect` and `uk-effects` output should use the same split:
  print source-acquisition observations first, then blocking source-acquisition
  rejections only when the shared compile-record classifier says they block.
- Available-but-malformed affecting-act XML is a source pathology, not a
  generic no-op or missing-source artifact. UK compile, `uk-replay`,
  `uk-effects`, `uk-effect`, `uk-bench`, candidates, and evidence/proof bundles
  must preserve it as blocking `uk_affecting_act_xml_parse_rejected` with
  `phase=parse`, while absent affecting-act XML remains
  `uk_affecting_act_xml_missing_rejected` with `phase=acquisition`.
- Present-but-too-small affecting-act XML is acquisition evidence, not parse
  evidence. UK compile/replay and inspection tools must emit blocking
  `uk_affecting_act_xml_too_small_rejected` with `phase=acquisition` and the
  observed source size instead of attempting XML parse and losing the truncated
  source witness.
- Missing affecting-act XML is blocking source-acquisition evidence only for
  effect rows that can legitimately require affecting source for replay:
  structural replay rows and the narrow supported nonstructural replay families.
  Commencement and other unsupported nonstructural rows must not inflate
  source-acquisition blocker counts merely because their affecting instrument is
  not cached; they are classified in the nonstructural/source-shape lane instead.
- `uk-effects` must support direct post-summary filtering by typed diagnostic
  family (`--source-pathology`, `--lowering-rule`,
  `--source-acquisition-rule`, `--manual-compile-status`, and
  `--manual-compile-rule`). Benchmark triage should not require exporting every
  effect row and post-processing JSON just to inspect one blocker family; these
  filters run before `--limit` so bounded reports remain representative of the
  selected diagnostic family. `--evidence-jsonl` requires a manual-frontier
  status or rule filter and writes the selected rows as a compact work queue
  with source witness, not as executable replay input. A rule-only manual
  frontier export is valid because rule IDs are the stable work-queue partition.
- `uk-effect`, `uk-effects`, `uk-candidates`, UK bench, and UK prefetch reports
  use the same blocking classifier as `uk-replay`/proof bundles for
  feed/source/residual compiler rows. This prevents
  `strict_disposition=record` observations from becoming parse, compile, or
  acquisition rejections when copied between tooling surfaces.
- Payloadless UK text-level operations must still carry a recoverable lowered
  witness. Source-first authority filtering is allowed to reject metadata-only
  text patches, but it must not reject a `text_replace` / `text_repeal` merely
  because the operation has no structural payload node to host the witness.
- A single-target `words omitted` / `word omitted` / `words repealed` /
  `word repealed` effect whose affecting XML payload is only one quoted fragment
  may lower to a typed `text_repeal` with rewrite rule
  `uk_effect_quote_only_omission_payload_text_patch`. The effect feed owns the
  action and target; the affecting XML owns the exact deleted fragment. This
  rule must not fire when surrounding residue contains substantive instruction
  text or when the effect expands to multiple targets.
- A single-target `words repealed` / `word repealed` / `words omitted` /
  `word omitted` effect whose source row says `the words "X" in paragraph (c)`
  may lower to a typed `text_repeal` only when the source child kind/label
  exactly matches the effect-feed target leaf. The owned rule is
  `uk_effect_child_qualified_word_omission_text_patch`; mismatch cases remain
  blocked overlap parse failures, not live-tree retargeting.
- UK fragment substitution parsing accepts ordinary drafting variants such as
  `for "X" in both places where it occurs, substitute "Y"`,
  `for "X" in both places where it occurs, there is substituted "Y"`, and
  source quote defects where a closing curly quote is used as the opening quote
  before the replacement. These are parse-lane recoveries for explicit
  instruction text, not authority-mode or replay-time guesses.
- UK fragment insertion parsing must preserve insert semantics. A word-level
  instruction such as `for "6" insert "12"` compiles to a text patch whose
  preimage is `6` and whose replacement is `6 12`, with rule
  `uk_effect_for_insert_text_insertion_patch`; it must not silently turn the
  insertion into a replacement of `6` by `12`.
- UK also has a narrow irregular formula where the source says `for "X" there
  is inserted "Y"`. This is not the same as `for "X" insert "Y"`: it lowers to
  replacement of `X` by `Y` with rule
  `uk_effect_for_there_is_inserted_replacement_text_patch`. The family is owned
  separately because generic insertion lowering would wrongly produce `X Y`.
  Witness: `asp/2003/1` `s. 17(3)`, affected by `asp/2005/12` Sch. 1
  para. 11(b), where enacted text says `paragraphs (a) to (h)` and the intended
  endpoint becomes `(i)`.
- Conversely, unquoted preimage substitutions such as `for the period specified
  in section 50(2) ... there is substituted the period of four years` are not
  parser work by themselves. They are classified as
  `uk_manual_frontier_unquoted_preimage_substitution_source_insufficient`
  unless a separate source/preimage claim proves the old text to be replaced.
  Witness: `asp/2003/1` `s. 50(2)`, affected by `ssi/2003/607` art. 2.
- UK range substitution parsing preserves ordinal anchors in text spans. An
  instruction such as `for the words from "the" where it second occurs to the
  end substitute "..."` lowers to `TEXT_FROM_the_TO_END` with occurrence `2`,
  not an unbounded first-match patch. Witness: `asp/2000/11`, affected by
  `asp/2010/13 s. 106(2)(a)`.
- The same ordinal range family accepts the comma-separated wording `from
  "X", where it second occurs, to "Y" substitute "Z"` as
  `uk_effect_range_occurrence_substitution_text_patch`; this is still a
  bounded text range and does not cover source-carried structured replacement
  blocks. Witness: `ukpga/1985/66` affected `s. 20(1)` by
  `asp/2014/11 Sch. 3 para. 15(a)`.
- UK range-to-end substitution also accepts the drafting form `there is
  substituted`, not only imperative `substitute`. An instruction such as `for
  the words from "member" to the end there is substituted "..."` lowers to
  `TEXT_FROM_member_TO_END` with rule
  `uk_effect_range_to_end_there_is_substituted_text_patch`. Witness:
  `asp/2000/11`, affected by `asp/2006/10 Sch. 6 para. 9(7)`.
- Passive quoted-word substitution tolerates source transport spacing damage
  where `there` is directly adjacent to the closing quote of the preimage, e.g.
  `for the words "X"there shall be substituted the words "Y"`. Lowering emits
  `uk_effect_missing_space_there_is_substituted_text_patch`, preserving the
  quoted preimage and replacement rather than normalizing arbitrary surrounding
  text. Witness: `ukpga/1970/9` affected `s. 61(4)` by
  `ukpga/1989/26 s. 152(4)(7)`.
- All-occurrence passive substitutions accept the replacement marker `the
  words` after `there shall be substituted`, e.g. `for the word "assessment",
  in each place where it occurs, there shall be substituted the words "..."`.
  This remains under `uk_effect_all_occurrences_substitution_text_patch` and
  emits one all-occurrences text patch scoped to the affected provision.
  Witness: `ukpga/1970/9` affected `s. 55` by
  `ukpga/1994/9 Sch. 19 para. 18(2)`.
- UK range repeal parsing preserves parenthesized ordinal start anchors. An
  instruction such as `the words from "in" (where first occurring) to "Act" are
  repealed` lowers to `TEXT_FROM_in_TO_Act` with occurrence `1`, not a
  structural delete and not an unbounded first-match text patch. Witness:
  `asp/2001/2`, affected by `asp/2005/12 s. 51(8)(a)`.
- Effect inspection classifies text-patch rows whose explicit target exists
  but whose non-synthetic selector preimage is absent from both base and oracle
  target text surfaces as
  `text_patch_preimage_absent_from_target_surfaces`. This is compare/frontier
  evidence, not replay recovery; synthetic selectors such as `TEXT_FROM__TO_END`
  are excluded because they describe contextual spans rather than literal
  preimages. Manual-frontier triage must not label these rows deterministic
  frontend support merely because they lower to a text patch; it classifies
  them as `uk_manual_frontier_text_patch_preimage_chain_gap` /
  `source_insufficient` until the missing intermediate source chain is acquired
  or otherwise proved.
- Effect inspection must not demote a chained replay rewrite merely because
  the enacted target was absent and the current oracle already contains the
  replacement. If an explicit text-rewrite lowering rule such as
  `uk_effect_wherever_occurring_substitution_text_patch` targets a provision
  introduced after enactment, the preimage can be consumed between enactment
  and current oracle. When the base target is absent, the oracle target exists,
  the literal preimage is absent, and the literal replacement is present in the
  oracle target, classify the compare shape as
  `uk_compare_text_patch_preimage_consumed_by_replay_chain`. This remains a
  core candidate and is compare/frontier evidence only; it does not change
  replay semantics or authorize same-target absent-preimage rewrites.
- Source-pathology target-depth checks are path-aware. A reference like
  `in paragraph 3` may validly lower to `schedule:3/paragraph:3`; it is not
  automatically a `misselected_target_context` merely because section-local
  paragraph targets usually sit below a subsection. Under-depth claims remain
  valid when source text names a subsection or section-local paragraph but the
  lowered target only names the section.
- Replay distinguishes generic missing text selectors from idempotent-looking
  text rewrites. If a `text_replace` selector is absent but its replacement text
  is already present in the target subtree, replay emits
  `uk_replay_text_match_already_rewritten` and still makes no mutation. This is
  evidence classification only; it does not authorize assuming the skipped
  operation was legally redundant.
- Replay may recover a text selector when the only mismatch is effect-feed
  citation punctuation spacing, for example `c.14` versus source text `c. 14`.
  The same bounded rule covers the reverse spacing direction, such as
  `c. 29` versus source text `c.29`, and trailing selector whitespace before
  punctuation that belongs to the host provision rather than the selected
  source phrase.
  The recovery is intentionally narrow and must emit
  `uk_replay_text_match_punctuation_space_normalized` with
  `family=text_match_recovery`, `blocking=false`, and
  `strict_disposition=record`. This is a replay-time source typography
  normalization, not fuzzy text matching. Current witnesses include
  `asp/2000/1` effects `key-cf1e14a783fa6f41144915ac658e4046` and
  `key-8be5a6cdf965a46ea436730a21e7f1db`, plus `asp/2000/7` effect
  `key-baf5fe3bb0e52c712c284bec5648920c`.
- Replay may recover a text selector when the only mismatch is word-internal
  apostrophe/hyphen elision between the effect-feed selector and source/XML
  text, for example `tenant's son-in-law` versus `tenants soninlaw`. The rule
  `uk_replay_text_match_word_punctuation_elided` is limited to apostrophe-like
  and hyphen-like marks between word characters; it must not match whitespace,
  arbitrary punctuation, or reordered words. This is replay evidence with
  `family=text_match_recovery`, `blocking=false`, and
  `strict_disposition=record`.
- Replay may recover a DELETE-only omission selector when the source quotes a
  simple phrase with a trailing comma, but the target text carries that comma
  immediately before the phrase. Example: source says omit `Part 4,` while the
  resolved target says `), Part 4 is amended`. The rule
  `uk_replay_text_match_rotated_trailing_comma_omission` may delete only the
  phrase (`Part 4`) and preserve the host comma. It requires exact structural
  target resolution, selector `occurrence=0`, a simple alphanumeric phrase,
  exactly one `, <phrase>` rotated preimage, and exactly one phrase preimage in
  the explicit target subtree. Otherwise replay must leave the operation as a
  blocking text-match gap. This is replay evidence with
  `family=text_match_recovery`, `source_shape=trailing_comma_rotated_before_phrase`,
  `blocking=false`, and `strict_disposition=record`. Corpus witnesses include
  `uksi/2020/1520` regs. 5(13)(a) and 5(14)(a) against `ukpga/2020/17`.
- Replay may recover an insertion-style text selector when the source quotes a
  short numeric list anchor with a trailing comma, but the resolved target has
  that same item uniquely as the final item before `and` or `or`. Example:
  source says insert after `28,` while the enacted target surface says
  `27, 28 and 60`. The rule
  `uk_replay_numeric_list_trailing_comma_anchor_normalized` may replace only
  the numeric/alphanumeric token (`28`), preserving the exact resolved target
  and avoiding same-target preimage-drift classification. It requires selector
  occurrence `0` or `1`, no end occurrence, replacement text beginning with the
  same anchor plus comma, no exact selector preimage, and exactly one eligible
  token before a conjunction in the explicit target subtree. Ambiguous or
  non-list contexts remain blocking gaps or preimage-drift findings. This is
  replay evidence with `family=text_match_recovery`,
  `source_shape=numeric_list_trailing_comma_before_conjunction`,
  `blocking=false`, and `strict_disposition=record`. Current witness:
  `asp/2000/5` / `asp/2003/9` Sch. 13 para. 2(a)(iii), section `17(1)`.
- Replay may recover a contextual word selector anchor kind when the explicit
  target subtree has no exact source kind but has exactly one same-label child
  among provision-like child kinds. This covers UK schedule wording such as
  `paragraph (c)` where the parsed target children are `item c`. The recovery
  must emit `uk_replay_contextual_word_anchor_kind_normalized` with
  `family=text_match_recovery`, `blocking=false`, and
  `strict_disposition=record`; if there is not exactly one same-label anchor,
  replay must leave the operation unresolved.
- Parser lowering must preserve nested contextual word anchors when the source
  says a word immediately follows a local child such as `subsection (4)(a)`,
  `paragraph (c)(ii)`, or `sub-paragraph (a)(i)`. These lower to explicit
  contextual selectors like
  `TEXT_WORD_and_IMMEDIATELY_FOLLOWING_subparagraph_ii`, not the old generic
  `..._TARGET` placeholder. If replay cannot find a unique child anchor under
  the explicit target subtree, the row remains a blocking text-match miss rather
  than widening the target.
- The contextual word-repeal family also accepts `which appears immediately
  after paragraph (a)` and `which immediately follows paragraph (b)`. Both
  lower to
  `TEXT_WORD_<word>_IMMEDIATELY_FOLLOWING_paragraph_<label>` under
  `uk_effect_contextual_adjacent_word_repeal_text_patch`, preserving the
  source-owned local anchor. Current witnesses: `asp/2000/4` affected
  `s. 19(5)` by `asp/2007/10 s. 57(4)(b)(i)` and `asp/2000/5` affected
  `s. 73(1)` by `asp/2003/9 Sch. 13 para. 13(a)(ii)`.
- The same contextual family covers imperative omission wording such as
  `omit the "or" following paragraph (b)`. It lowers through
  `uk_effect_contextual_adjacent_word_omit_text_patch` to the same explicit
  child-anchor selector rather than a bare deletion of the quoted word from
  the whole parent target. Lowering records the rule as a nonblocking
  `text_rewrite_lowering` observation with strict disposition `record`.
  Current witness: `asp/2001/2` affected
  `s. 39(1)(b)` by `asp/2019/17 Sch. para. 3(4)(a)`, where a prior `or`
  omission must not delete citation connectors inside paragraph (b).
- Parser lowering must not compile `the definition of "X" is repealed`,
  declarative plural wording such as `the definitions of "X" and "Y" are
  repealed`, or imperative wording such as `omit the definition(s) of "X" [and
  "Y"]` as a bare deletion of the words `X`. It lowers each quoted definition
  term to `TEXT_DEFINITION_ENTRY_X`, and replay may delete only a uniquely
  bounded definition entry for that term. This prevents a definition repeal
  from removing the same words inside another definition or phrase needed by a
  later same-target operation. Current declarative plural witness:
  `asp/2001/13` effect `key-0d4201398b6c7a4a16f85850e264338a`.
  - if the target subtree does not contain exactly one bounded definition entry,
    replay emits blocking `uk_replay_definition_entry_shape_gap`; it must not
    fall back to deleting the bare term
- Parser lowering may also compile `for the definition of "X", substitute- ...`
  as `TEXT_DEFINITION_ENTRY_X` using
  `uk_effect_definition_entry_substitution_text_patch`; the comma after the
  quoted anchor is punctuation in the source formula, not a different target
  family. Current corpus witness: `asp/2001/10`
  `key-3c8a483c35fb24fd68e1677ad672502a`.
- If legislation.gov.uk splits that same formula across a parent source
  instruction and a `BlockAmendment`, lowering may combine only those
  source-local facts: the parent must explicitly say `for the definition of
  "X" there is substituted`, and the block payload must itself be a complete
  definition entry. The resulting selector is `TEXT_DEFINITION_ENTRY_X` with
  rule `uk_effect_source_carried_definition_entry_substitution_text_patch`.
  This is not authority to infer the old definition term from live text, nor to
  treat a non-definition payload as a definition substitution. The source-local
  boundary is part of the rule: an unrelated sibling definition substitution in
  the same schedule/cross-heading must not be smuggled into the current block
  payload. Current corpus witnesses: `asp/2000/7` effect
  `key-b7c7cdf19629dcd25fde12967bca8c51` from `asp/2010/11 Sch. 1 para. 7(a)`;
  negative boundary witness `asp/2000/4`
  `key-78605c6a5376f3a9f6955c985964d597`, where the correct lowering is an
  anchored definition insertion after `Mental Welfare Commission`, not a
  substitution of `hospital`.
- The same source-local split is owned for generic quoted text patches when
  the parent source instruction supplies the exact quoted anchor/preimage and
  the extracted `BlockAmendment` supplies only the payload. `after "X" there
  is inserted- <payload>` lowers to a replacement of `X` with `X<payload>`
  under `uk_effect_source_carried_after_quoted_anchor_insert_text_patch`;
  `for "X" there is substituted- <payload>` lowers to replacement of `X`
  under `uk_effect_source_carried_quoted_text_substitution_text_patch`.
  These rules require the parent source witness and do not infer anchors from
  live text. Current corpus witnesses: `asp/2001/2`
  `key-5c591c6e000ad938236c4a9711426132` and `asp/2000/11`
  `key-aeafeef7fe358d46b1fd8715e2aa27ef`.
  If that quoted-substitution payload itself begins with a visible consecutive
  roman child run, replay may materialize those children under the explicit
  paragraph-like target via
  `uk_replay_source_carried_labeled_child_text_substitution_recovered`. This
  is not oracle alignment: the source payload must carry the labels, the target
  node must have no existing children, and strict mode disposition is `block`.
  The `asp/2000/11` witness above creates `s. 11(4)(b)(i)` and `(ii)` before
  later 2012 effects target those children.
  If the same parent instruction itself contains the inserted quoted words,
  as in `after "weapon" insert "or corrosive substance"` followed by child
  rows listing affected provisions, the inline quoted words are the payload;
  the child row is target evidence, not inserted text. The rule must not carry
  definition-entry context from a broader ancestor block unless the local
  instruction text names that definition. Corpus witness: `ukpga/2020/17`
  Schedule 22 paragraph 83(a)-(c).
  A related block-payload rule handles parent instructions that say `the
  following words are repealed`: the `BlockAmendment` payload lowers to the
  exact deletion preimage under
  `uk_effect_source_carried_following_words_repeal_text_patch`. This is not a
  structural payload and not a license to synthesize omitted words from the
  target. Current corpus witness: `asp/2001/2`
  `key-34becb61c5e46e181f9889c8a8a91de1` from `asp/2019/17`
  `sch. para. 3(9)(b)`.
  If the same parent source instruction also names a definition entry, the
  quoted-anchor insertion is scoped to
  `TEXT_IN_DEFINITION_<term>/AFTER/<anchor>` rather than lowered as a bare
  text patch against the whole subsection. This prevents a generic anchor such
  as `authority` from rebinding to an earlier definition entry merely because
  that word appears first in the live target. Strict mode records the scoped
  source-context elaboration; it does not authorize inferring the definition
  term from live text.
  Generic child rows under a parent instruction that explicitly names a
  definition entry are also scoped to that source-carried definition. A child
  row such as `the words from "in" ... to "Act" are repealed` lowers to
  `TEXT_IN_DEFINITION_<term>/FROM/<start>/TO/<end>` under
  `uk_effect_source_parent_definition_range_text_patch`; a child row such as
  `after "by" there is inserted "(a)"` lowers to
  `TEXT_IN_DEFINITION_<term>/AFTER/<anchor>` under
  `uk_effect_source_parent_definition_after_quoted_anchor_insert_text_patch`.
  The current corpus witness is `asp/2001/2` affected by `asp/2005/12`
  `s.51(8)(a)-(b)`. These are source-context elaborations, not live-text
  guesses: strict mode records the source parent context and blocks if replay
  cannot find exactly one matching definition entry.
  If the same parent instruction also says the definition child is paragraph
  `(a)` and the effect feed target is `s. N(a)`, the row lowers to
  `TEXT_IN_DEFINITION_CHILD_PARAGRAPH_<term>/a/AFTER/<anchor>` under
  `uk_effect_source_parent_definition_child_after_quoted_anchor_insert_text_patch`.
  Lowering separately records
  `uk_effect_source_parent_definition_child_target_refined`: the operation
  targets only the containing section, while the selector retains the child
  paragraph scope. This is a source-context elaboration, not a replay fallback.
  Current witness: `asp/2001/2` `key-259a386240ffcc043cf39d2fb13bc38f`.
- Parser lowering may inherit definition-list context for child rows that only
  contain a quoted term. If the parent source instruction explicitly says
  `omit the definitions of-` and each child row is just `"X",`, the child row
  lowers to `TEXT_DEFINITION_ENTRY_X` under
  `uk_effect_quote_only_definition_list_omission_text_patch`. Standalone
  quote-only omissions remain bare text deletions; the parent definition-list
  source witness is required.
- Replay definition-entry matching treats immediately following parenthetical
  alias text as part of the same bounded entry. This covers Welsh/translated
  definition aliases such as `"X" ("Y") means ...` without deleting a bare
  occurrence of `X` elsewhere in the target subsection.
- Replay definition-entry matching also treats a comma-delimited qualifier
  between the term and predicate as part of the same bounded entry, covering
  forms such as `"X", in relation to Y, means ...`. Successful application
  emits nonblocking `uk_replay_definition_entry_qualifier_phrase_normalized`
  with `family=definition_entry_predicate_recovery` and
  `strict_disposition=record`.
- Replay definition-entry matching may normalize an orphan comma immediately
  after a definition-entry separator before a quoted term, such as
  `; , "X", in relation to Y, means ...`. This is a replay-surface seam caused
  by earlier source-carried definition insertion/substitution ordering, not a
  licence to match arbitrary comma-separated prose. Successful application
  emits nonblocking
  `uk_replay_definition_entry_orphan_separator_normalized` with
  `family=definition_entry_separator_recovery` and
  `strict_disposition=record`. Current corpus witness: `asp/2000/11`
  `key-15ee4348695468e659eb4c241bb98b57_1`, where `police member` was inserted
  by `asp/2006/10` and repealed by `asp/2012/8`.
- Replay definition-entry matching treats `shall be construed` as a bounded
  definition predicate variant when and only when the named term resolves to
  exactly one definition entry in the target subtree. Successful application
  emits nonblocking
  `uk_replay_definition_predicate_shall_construed_normalized` with
  `family=definition_entry_predicate_recovery`,
  `strict_disposition=record`, and `quirks_disposition=record`. Current corpus
  witnesses include `asp/2001/2` effects
  `key-ebf6b73b896bab897c15ae554c0db64c`,
  `key-02150edb4f24177b310a05305b2617c8`, and
  `key-570e7790f9e34114d99beba23be1565a` from `asp/2019/17`.
  The rule does not authorize inserting an absent definition entry or treating
  `substitute` as `insert`. For repeal/delete operations only, if the named
  definition term is already absent from the target subtree, replay emits
  nonblocking `uk_replay_definition_entry_already_absent_observed`; if the term
  is present but cannot be uniquely bounded as a definition entry, replay
  remains blocking `uk_replay_definition_entry_shape_gap`.
- Replay definition-boundary matching also treats plural conjoined predicates
  such as `"A" and "B" have the same meaning as ...`, `have the meaning`, and
  `are to be construed` as definition starts. This is not a recovery rule; it
  prevents `TEXT_AFTER_DEFINITION_*` and definition-entry range selectors from
  overruning into the next conjoined definition entry.
- Parser lowering may compile `in the definition of "X", omit paragraph (d)` or
  `in the definition of "X", for paragraph (c) substitute ...` to
  `TEXT_DEFINITION_CHILD_PARAGRAPH_X<US>label`. Replay may rewrite only a
  uniquely bounded child segment inside the named definition entry.
  - preferred source-tree shape: UK XML `OrderedList Type="alpha"` under a
    definition entry is preserved as target-owned `item` children under
    `uk_definition_ordered_list_child_preserved`, carrying the source
    `definition_term` and `definition_child_label` in attrs rather than a
    synthetic public IR label. Replay may delete or replace that explicit child
    only when term and label identify exactly one preserved child.
    The parser emits a nonblocking source-parse observation with the same rule
    ID, including count and sample attrs, so the structure-preserving repair is
    visible in replay, EID, effects, and bench evidence instead of being a
    hidden grafter heuristic.
  - `definition_child_label` must use the official `ListItem@NumberOverride`
    when present. The parser may synthesize sequential alphabetic labels only
    when the source list item does not carry an override. This matters for UK
    amendment patterns such as inserted definition paragraphs `(aa)` and `(ab)`;
    silently relabelling them to `(b)` and `(c)` destroys the source identity
    needed by later text patches and replay/oracle comparison.
  - Source-carried child insertions such as `after that paragraph, insert—`
    and `at the end of paragraph (b), insert—` may lower to
    `TEXT_AFTER_DEFINITION_PARAGRAPH_<term>_AFTER_<label>` only when the
    affecting-source parent proves the definition term and anchor child. The
    lowering rule is
    `uk_effect_source_carried_definition_child_insert_text_patch`. Replay may
    insert preserved definition children under the matched parent and, for
    `; or`/`; and` payload prefixes, append that connector to the explicit
    anchor child before inserting the new child. Without this parent source
    context, the row remains a deterministic frontend/manual frontier item
    rather than being guessed from the extracted child text alone.
  - Source-carried child text omissions such as parent source `In the
    definition of "local transport authority"...` plus child row `in paragraph
    (a), omit "or"` lower to
    `TEXT_IN_DEFINITION_CHILD_PARAGRAPH_<term>/<label>/<text>` under
    `uk_effect_source_carried_definition_child_text_omission_text_patch`.
    Replay mutates only the uniquely preserved definition child whose
    `definition_term` and `definition_child_label` match the source facts. It
    must not lower the quoted word as a bare subsection-wide text deletion,
    because short words such as `or` can otherwise corrupt unrelated words such
    as `authority`, `transport`, or `charging`. Current corpus witness:
    `asp/2001/2` affected by `ssi/2024/161` `art. 6(2)(a)`.
    The parent definition context is deliberately local: broad containers such
    as a `Pblock`/`Body` containing unrelated sibling amendment paragraphs must
    not donate a definition term to a normal structural paragraph row. Current
    negative corpus witness: `ukpga/2022/10` affected by `ukpga/2023/56`
    `s. 175(b)`, where `in paragraph (b), omit "or continued"` remains an
    ordinary paragraph-b text omission rather than a definition-child selector.
  - bilingual definition headings such as `“private sector employer”
    (“cyflogwr sector preifat”) means...` preserve the first/source-language
    quoted term as `definition_term`; the parenthesized translation is source
    context, not the replay address.
  - visible inline XML tags such as `Citation`, `CitationSubRef`, and `Term`
    are not standalone legal units for UK replay addressing, but their text is
    legal text of the host provision. The parser must preserve that visible
    inline text in the host provision while continuing to exclude those inline
    tags from EID identity. When such inline text is present, it emits
    nonblocking source-parse observation `uk_visible_inline_text_preserved`
    with count and samples. This prevents definition-anchor rewrites from
    operating on a source surface where citation titles have silently vanished,
    for example `“2013 Act” means the <Citation>Local Government (Democracy)
    (Wales) Act 2013</Citation>;`.
  - `<Text id="p...">` fragment anchors inside amendment payloads are likewise
    not standalone legal units for EID scoring. The grafter must not count a
    `Text` element's own `id`/`eId` as replay-addressable identity, while still
    recursing through surrounding amendment containers so any genuine structural
    descendants remain visible. Current witness: `asc/2024/6` current XML has
    formula text anchors `p10001`... under `BlockAmendment PartialRefs`; those
    IDs are source-local fragment anchors, not sections, schedules, or legal
    paragraphs of the host Act.
  - fallback source-tree shape: for older flattened surfaces, replay may still
    use the existing semicolon-delimited paragraph ordinal path, but only when
    exactly one text carrier and one bounded definition entry exist.
  - if the target subtree does not have exactly one replay-visible text carrier
    or the named definition/child ordinal cannot be bounded, replay emits
    blocking `uk_replay_definition_child_shape_gap`; it must not delete the
    bare child label or rewrite the whole definition entry
- Effect metadata that points at `Act` / `/whole_act` must not override source
  text that names a different Act as the actual amendment target. Lowering must
  emit blocking `uk_effect_external_act_target_rejected` with the source-named
  Act title and skip the row rather than sending a destructive `/whole_act`
  text operation to replay.
  Manual-frontier triage classifies this as
  `uk_manual_frontier_external_act_target_out_of_scope`, because it is not a
  manual compilation opportunity for the current statute; it belongs to the
  source-named target Act's replay graph.
  - current witness: `asp/2002/11` Schedule 6 paragraphs amending schedules to
    the Town and Country Planning (Scotland) Act 1997 and related external Acts
- A partial whole-Act repeal such as `The whole Act (other than sections 13 and
  16) is repealed` is a broad negative target scope. Lowering must emit
  blocking `uk_effect_partial_whole_act_repeal_rejected` with the exception
  provisions rather than compiling `/whole_act` replace/repeal or expanding all
  live children except the exceptions.
  - current witness: `asp/2003/5` / `asp/2007/14 Sch. 4 para. 42`
- UK insertion ordering must distinguish ordinary alphabetic item suffixes
  from Roman numerals by sibling scheme. A schedule item batch such as
  `2(da)-(dk)` belongs after item `d` and before item `e`; labels like `dc`,
  `di`, and `dl` must not be romanized into numeric order when the peer set is
  an alphabetic item family.
  - current witness: `asp/2003/5` / `asp/2009/9 Sch. 5 para. 4(3)` and
    `ssi/2010/421 Sch. para. 2(3)`
- The same insertion ordering helper must also preserve Roman-suffix labels
  when the peer scheme is Roman. Labels such as `iia` and `iiia` sort after
  `ii` and `iii` respectively, before the next Roman numeral. This is still
  deterministic ordering, not target recovery.
  - current witnesses: `asp/2002/17`, `asp/2003/1`, `asp/2003/11`, and
    `asp/2004/11`
- Explicit source insertion anchors override generic label-order placement.
  If extracted source says `After section 97 ... insert` and the effect target
  is `section 97ZA`, lowering must carry `preceding_eid=section-97` from the
  extracted source, not only from effect comments. For grouped inserts under
  one anchor, later siblings chain to the prior inserted target so `20A`,
  `20B`, `20C` remain in source order rather than repeatedly inserting after
  `20`.
  - chained insertion anchors must work for nested targets as well as
    top-level sections. The prior generated target eId is derived from the full
    canonical UK target address, and lowering records
    `uk_effect_chained_insertion_anchor_lowered` as nonblocking
    `target_resolution_recovery` evidence.
  - replay may emit nonblocking `uk_replay_source_anchored_order_observed`
    when this explicit source order conflicts with the generic label-order
    invariant; this is an invariant-model limitation, not a replay failure
  - current witness: `asp/2003/2` / `asp/2015/6`, where the oracle places
    `97ZA` immediately after `97` and before `97A`
  - current nested witness: `asp/2001/10` / `asp/2014/14 s. 7(2)(b)(iii)`,
    where one source clause inserts `section 35(3)(c)` and `(d)` after
    paragraph `(b)`
- If an insert target already exists and the existing target subtree has the
  same normalized text as the payload subtree, replay records nonblocking
  `uk_replay_existing_target_already_materialized` and performs no mutation.
  Conflicting same-target inserts record blocking
  `uk_replay_existing_target_conflict_gap` with existing/payload text previews;
  the idempotent rule is not permission to overwrite or ignore divergent
  payloads. Generic `uk_replay_existing_target_gap` remains only for
  existing-target inserts where replay cannot safely compare payload surface.
  If the normal target lookup misses an existing target because the explicit
  parent is wrapped in UK presentation containers such as `part`/`p1group`,
  replay may resolve the exact parent by derived eId, match only the same
  explicit leaf kind/label, and record
  `target_resolution_recovery=explicit_parent_leaf_same_kind_label`; this
  authorizes only existing-target adjudication, not insertion, replacement, or
  sibling search.
  If target lookup misses the existing child but insertion routing later
  resolves the parent, replay must re-check the resolved parent for the exact
  target leaf kind/label before mutating. A matching child is classified as
  already-materialized or conflict using the same existing-target rules; replay
  must not insert a duplicate sibling and then report a tree invariant
  violation after the fact.
  - current witness: `asp/2002/17` / `ssi/2011/141`, where paragraph `h` had
    already materialized before a later same-target insertion row
  - wrapped-parent conflict witness: `asp/2002/17` / `ssi/2011/141` section
    `4(2A)`, where a later source-anchored insertion would otherwise duplicate
    an existing subsection under a `part`/`p1group` section wrapper
  - conflict witnesses include `asp/2000/4` / `ssi/2011/211` schedule item
    `1(e)` and `asp/2002/17` / `asp/2007/3` section `11(3)`
  - parent-local duplicate-guard witness: `ukpga/1992/4` /
    `uksi/2014/3229 Sch. 4 para. 2(8)(a)(ii)`, where paragraph inserts for
    `s. 48A(2ZA)(c)` and `(d)` must not duplicate already-materialized
    children under the resolved subsection parent.
- An inserted crossheading compiled to bare `crossheading:` has no explicit
  identity or placement anchor. Replay records blocking
  `uk_replay_crossheading_target_gap` instead of misclassifying it as an
  ordinary duplicate target. A future fix should lower crossheading inserts to
  a typed facet/node with source-backed placement, usually paired with the
  adjacent inserted section.
  - current witness: `asp/2003/13` / `asp/2015/9`, inserted crossheadings
    paired with sections `257A`, `271A`, and `291A`
- When a text selector misses after replay has already applied a text patch to
  the same target path, replay must classify the miss as
  `uk_replay_text_patch_preimage_drift` and include the prior same-target text
  patch op IDs. This is still blocking for the skipped operation; it only
  separates composition/preimage drift from generic missing text so later
  source-order or manual-compile work has a precise queue.
  - if the replacement text is already present under alphanumeric
    normalization, replay records
    `uk_replay_text_match_replacement_normalized_present` instead. This remains
    evidence-only and applies no mutation; it prevents quote/punctuation
    surface differences from becoming a false blocking preimage-drift claim.
  - if more than one prior same-target text patch has already applied, replay
    records `uk_replay_text_patch_preimage_drift_multi_prior_same_target`
    because the unresolved composition problem is no longer a single-patch
    preimage drift
  - do not treat this class as permission to reorder same-date operations or
    apply fuzzy matching in replay
  - current witnesses show different causes: `asp/2001/8` has a repealed phrase
    absent from the base/oracle target surfaces after a same-source insertion.
    Earlier `asp/2001/2` samples exposed false ancestor-anchor borrowing for
    `at the appropriate place` definition-entry inserts; those rows now block
    during lowering as manual placement candidates rather than reaching replay
    as preimage drift.
  - a future fix belongs in phase-local lowering/source extraction only if it
    proves the narrower target or legal ordering from source evidence
- Synthetic text selectors such as `TEXT_FROM_*` or `TEXT_AFTER_*` are internal
  compiler placeholders, not literal statutory text. If such a selector reaches
  replay and misses outside the definition-entry special case, replay records
  `uk_replay_text_match_synthetic_selector_gap` rather than a generic
  `uk_replay_text_match_missing`.
- If the exact text selector misses but an alphanumeric-normalized selector is
  present in the target subtree, replay records
  `uk_replay_text_match_normalized_preimage_present_gap`. This proves a
  presentation/source-surface mismatch such as punctuation or citation styling,
  not authority to perform a fuzzy replacement.
- If the exact selector includes citation year/chapter tail text but the target
  subtree only contains the same selector with that citation tail missing,
  replay records `uk_replay_text_match_citation_tail_surface_gap`. This is a
  text-surface/source-shape diagnosis; replay must not silently omit citation
  tail text to make a mutation executable.
- If the selector itself is non-substantive shell text, such as dot leaders,
  replay records `uk_replay_text_match_non_substantive_selector_gap`. Replay
  must not treat punctuation-only source shells as executable legal text.
- If a selector surface combines multiple quoted or otherwise separated source
  fragments into one contiguous text patch, replay records
  `uk_replay_text_match_multi_fragment_selector_gap`. Replay must not delete or
  substitute separated spans from a collapsed selector; lowering must emit
  separately owned operations or a manual compile claim.
  A deterministic subfamily is now owned for `the words "A", "B" and "C" are
  repealed/omitted`: lowering emits
  `uk_effect_multi_quoted_word_repeal_text_patches` and one text delete per
  quoted fragment. Current witness: `asp/2000/4` affected `s. 70(1)` by
  `asp/2007/10` s. 60(8)(a).
- When a text operation reaches a live target but the target subtree has no
  replay-visible text at all, replay records
  `uk_replay_text_target_empty_surface_gap`. This is distinct from ordinary
  `uk_replay_text_match_missing`: the executor found the structural target, but
  there is no text surface on which the text patch could operate. Replay must
  not infer a parent/sibling target or inject the source phrase.
- When source explicitly targets a schedule paragraph's first subparagraph but
  the UK XML carries that first subparagraph as paragraph intro text with item
  children, replay may apply the exact text patch to the parent paragraph text
  only under `uk_replay_implicit_first_subparagraph_parent_text_recovered`.
  Preconditions are narrow: the leaf target is `subparagraph:1`, the parent
  `paragraph` exists, no structural child `subparagraph:1` exists, and the
  parent text contains the exact selector. The recovery is a nonblocking
  quirks-mode observation with `strict_disposition=block`; it does not authorize
  sibling/child mutation or any other parent fallback.
- When the effects feed targets a direct section paragraph such as `s. 48(a)`
  but the source XML represents the preimage inside a direct child carrier
  instead of an addressable paragraph, replay may apply the exact text patch to
  that child only under `uk_replay_direct_section_paragraph_child_text_recovered`.
  Preconditions are intentionally narrow: the section exists, it has no
  paragraph children, exactly one direct child contains enough occurrences of
  the selector to satisfy the source ordinal, and the patch applies inside that
  child subtree. Ambiguous child carriers still emit
  `uk_replay_direct_section_paragraph_carrier_gap`. Current witness:
  `asp/2001/2`, `asp/2005/12` s. 51(2)(a), "after second authority ... (i)".
  Strict mode blocks the recovery.
- Same-source ordinal text-patch overlap blocking is target-text-aware. If an
  ordinal selector such as `scheme` appears inside broader same-source patches
  but the claimed ordinal occurrence is disjoint in the base target text,
  replay records nonblocking
  `uk_replay_same_source_text_patch_overlap_disjoint`, orders that ordinal patch
  before the broader same-source patches that would disturb its base-text
  occurrence count, and allows the operation.
  This prepare-time ordering is instance-preserving: repeated effect-feed IDs
  may lower to multiple legal operations, and topological ordering must never
  collapse those operation instances by `op_id`. Current witness:
  `asp/2000/4`, where unrelated same-source text ordering previously caused
  duplicate repeal-table targets from `asp/2003/13` Sch. 5 Pt. 1 to replay as
  repeated copies of the last target.
  If the base target cannot witness disjointness, or the claimed occurrence
  overlaps a broader same-source patch, replay still blocks under
  `uk_replay_same_source_text_patch_overlap_blocked`.
- A valid single-segment section/article/rule/regulation target is not
  malformed merely because the body tree is organized under part/chapter
  wrappers. If the target is absent but bracketed by existing section-like
  labels, replay records `uk_replay_missing_sectionlike_range_gap` rather than
  `uk_replay_malformed_target_gap` or generic `uk_replay_repealed_target_gap`.
  This keeps valid UK alphanumeric labels such as `6A`/`6B`/`6C` out of the
  malformed-target bucket while still refusing to search beyond the explicit
  target or assert legal repeal without source proof.
- Malformed UK targets are split by the surface reason when replay can prove it
  without target fallback: bracket placeholder labels
  (`uk_replay_malformed_target_placeholder_label_gap`), note/crossheading labels
  lowered as numbered descendants
  (`uk_replay_malformed_target_note_or_crossheading_gap`), invalid root
  sectionlike labels (`uk_replay_malformed_target_sectionlike_label_gap`),
  unlabeled schedule roots
  (`uk_replay_malformed_target_schedule_root_label_gap`), and address
  granularity collapses where a descendant label was compiled into the wrong
  level (`uk_replay_malformed_target_granularity_collapse_gap`). All remain
  blocking source/lowering gaps; replay must not reinterpret the target as a
  sibling, parent, crossheading facet, or textual descendant.
  - invalid sectionlike root labels are recognized even when the malformed
    label has descendants, for example a parser lowering phrase fragments into
    `section:appt/subsection:day`; this must not be hidden as an ordinary
    missing-parent shape gap
- If a replace payload leaf does not match the lowered target leaf, replay
  records `uk_replay_replace_payload_target_leaf_mismatch_gap` and performs no
  mutation. This is distinct from a malformed address: the target path can be
  syntactically meaningful, but source extraction/lowering produced a payload
  whose legal-unit kind or label is not owned by that target.
- If the grandparent target exists but the immediate parent target is absent,
  replay records `uk_replay_missing_parent_grandparent_present_gap` rather than
  generic `uk_replay_missing_parent_shape_gap`. This preserves the narrower
  source/lowering problem: the operation is anchored inside a live branch, but
  a required intermediate provision is missing and replay must not synthesize
  it silently.
- If a root-level parent such as `section:9` is absent for a descendant target
  such as `section:9/subsection:1`, replay records
  `uk_replay_missing_root_parent_shape_gap`. This is a different source/live
  shape problem from an absent intermediate child under an existing branch.
- If the parent exists and the target leaf is absent but bracketed by sibling
  labels or blank placeholder structure, replay records
  `uk_replay_absent_sibling_range_gap`, not generic
  `uk_replay_repealed_target_gap`. This proves only an interstitial
  target/source-shape gap, not legal repeal, and replay must perform no
  fallback insertion/replacement/deletion.
- If a schedule root or descendant branch is absent without a prior
  repealed-prefix witness, replay records `uk_replay_missing_schedule_range_gap`
  or `uk_replay_missing_schedule_branch_gap`. These classes preserve that the
  source/lowering/live-tree surface only proves an absent schedule shape; they
  must not be used to assert legal repeal or to search another schedule.
- A direct schedule paragraph target under a schedule that is actually
  partitioned into parts/chapters/divisions is a source/lowering context gap,
  not permission to search every partition for the paragraph. Replay records
  `uk_replay_schedule_partition_target_gap` and performs no mutation until the
  source supplies or lowering proves the missing partition context.
  - schedules partitioned specifically by `part` record the narrower
    `uk_replay_schedule_partition_part_target_gap`
  - current witness family: `asp/2002/11`, where later schedule repeals lower
    to paths such as `schedule:2/paragraph:80` although the live schedule is
    partitioned
- A metadata/source reference such as `Sch. 4 Pt 1` names a schedule part. It
  must lower to `schedule:4/part:1`, not to a paragraph/subparagraph chain
  formed from the literal token `Pt`.
  - rule: `uk_effect_schedule_part_abbreviation_target_normalized`
  - family: `target_shape_normalization`
  - strict disposition is `record`
  - current witness: `asp/2001/4` / `ssi/2002/134` `art. 2(7)`, where the
    affected provisions field is `Sch. 4 Pt 1`
- A schedule paragraph descendant target whose paragraph carrier is absent or
  represented by a legacy wrapper records
  `uk_replay_schedule_paragraph_carrier_gap`, not a generic missing-parent
  finding. Replay still performs no mutation; the fix belongs in source
  extraction/lowering or a named wrapper-normalization rule, not in parent
  fallback.
  - current witness family: `asp/2002/11` / `asp/2010/11`, e.g. schedule
    paragraph replacements lowered to `schedule:1/paragraph:1/subparagraph:3A`
- If the missing schedule paragraph carrier exists only as a `p1group` wrapper,
  replay records the narrower
  `uk_replay_schedule_p1group_wrapper_carrier_gap`. This identifies a legacy
  XML wrapper/ontology problem without silently rebinding the operation through
  that wrapper.
  A narrow owned exception is
  `uk_replay_schedule_p1group_paragraph_wrapper_resolved`: for an explicit
  schedule paragraph target, an unlabeled ordinal `p1group` may be traversed
  only when it has exactly one paragraph child and that child's label exactly
  matches the requested paragraph. Replay records the recovery and then applies
  the operation to the child paragraph/descendant. Multiple paragraph children,
  missing labels, or labelled `p1group` carriers remain blocked as wrapper
  carrier gaps.
  - current witness family: `asp/2002/11` / `asp/2010/11 Sch. 3 para. 13(b)`,
    targeting `schedule:1/paragraph:4/subparagraph:2b`
- A schedule paragraph descendant target under an unlabeled paragraph carrier is
  a source/XML ontology gap, not a malformed legal label. Replay records
  `uk_replay_schedule_unlabeled_paragraph_target_gap` and performs no mutation
  until lowering proves the intended carrier or a wrapper-normalization rule
  explicitly owns the unlabeled paragraph shape.
  - this is narrower than `uk_replay_schedule_paragraph_carrier_gap`: the
    schedule has paragraph descendants, but the paragraph carrier label needed
    by the legal address is absent in the source tree
- An Annex reference lowered as a Schedule target is a source-vocabulary
  mismatch. Replay records `uk_replay_annex_schedule_reference_gap` rather than
  treating the missing schedule as an ordinary malformed target. A fix belongs
  in UK target normalization with an explicit Annex/Schedule source witness.
- A text operation lowered to a missing schedule Part/Chapter while the source
  witness mentions paragraph/subparagraph/item text records
  `uk_replay_schedule_container_text_target_gap`. Replay must not search under
  the schedule for a matching descendant; lowering must prove the descendant
  target or emit a manual compile candidate.
- If source text names a descendant such as subsection `(1)(a)` but lowering
  collapses the path to `section:X/subsection:a`, replay records
  `uk_replay_subsection_descendant_target_collapse_gap`. This preserves the
  original target-resolution defect without treating the alphabetic descendant
  as a valid standalone subsection or broadening the search to a live unique
  child.
- Source-first authority diagnostics are applicability-aware. A compiled row
  that is not replay-applicable may still emit nonblocking
  `uk_effect_authority_filter_non_applicable_observed`, but it must not become
  blocking `uk_effect_authority_filter_rejected`, because no operation from
  that row would be admitted into replay under the selected applicability lens.
- UK bench evidence must preserve the same split in every surface. Nonblocking
  authority diagnostics are `authority_observations`; blocking authority
  filter failures are `authority_rejections` / blocking authority rejections.
  Reports, history rows, compare output, and compact row evidence must not
  label non-applicable observations as replay blockers.
- UK candidate reports are part of that same saved-bench evidence surface.
  `uk-candidates` rows, summaries, and fast text output must preserve saved
  benchmark authority observations separately from saved blocking authority
  rejections. The diagnostics sidecar can carry row records, but aggregate
  observation counts/rules must also survive in copied candidate reports.
- UK lowering diagnostics also distinguish replay blockers from unsupported
  nonstructural observations. Source-local compilation may emit
  `uk_effect_lowering_no_supported_action_rejected`, but replay-oriented
  callers must reclassify it as nonblocking with
  `uk_effect_nonreplay_lowering_observed` when the selected replay lens does
  not support or admit that nonstructural effect family, or when later
  source-pathology classification proves an out-of-scope lane such as as-if,
  commencement, or application-modification text. Renumbering,
  savings/transitional, excluded, and applied-with-modifications rows remain
  visible evidence; they are not counted as current replay blockers unless a
  supported nonstructural replay family claims them. New UK tooling should read
  `lowering_observation_*` for the full diagnostic lane and
  `blocking_lowering_rejection_*` for replay blockers; bare
  `lowering_rejection_*` is legacy naming. `uk-candidates` follows the same
  split in row JSON, summary JSON, and text summaries so candidate frontier
  triage cannot confuse nonblocking unsupported-effect evidence with a replay
  blocker.
- UK commencement instruments are a temporal/applicability source lane, not a
  structural text/tree mutation lane. If an extracted no-action source says the
  target provisions `shall come into force` / `come into force`, lowering
  records `uk_effect_commencement_source_rejected`, source adjudication
  classifies it as `commencement_effect_out_of_scope`, and manual frontier
  reports `uk_manual_frontier_commencement_effect_out_of_scope` instead of
  treating it as a generic unsupported replay action. A future temporal
  compiler may consume these rows, but structural replay must not synthesize
  text operations from commencement language.
- UK application-modification payloads are also outside unconditional
  structural replay. If an empty-effect row selects a `BlockAmendment` payload
  whose parent source formula says the target statute `shall apply ... subject
  to the modification that ...`, lowering records
  `uk_effect_application_modification_payload_rejected`, source adjudication
  classifies `application_modification_payload_out_of_scope`, and manual
  frontier reports
  `uk_manual_frontier_application_modification_payload_out_of_scope`. The
  payload may be useful for a future scoped application/temporal model, but it
  must not be replayed as a direct current-text insertion.
- Generic `uk_effect_lowering_no_ops_rejected` is a fallback, not a duplicate
  wrapper for every failed compile. If `compile_effect_to_ir_ops` already
  emitted a blocking lowering record for the effect, the pipeline must preserve
  that specific record and not add a second generic structural no-op rejection.
- `uk-replay` text diagnostics should label the all-row lowering histogram as
  `lowering observation rules`; `blocking lowering rules` is the replay-blocking
  subset.
- UK benchmark comparison classes must not count nonstructural-only current
  projection rows as core structural replay failures. If every parsed effect
  row for a statute is classified as `nonstructural_root_gap`, the row class is
  `nonstructural_current_projection` and `core_benchmark=false`. The row remains
  in the saved CSV/diagnostics/history evidence surfaces; it is removed only
  from structural replay averages. Mixed rows with `__none__`,
  instruction-text payloads, manual compile candidates, or any supported
  structural lane remain core unless another non-core comparison class applies.
- UK word-level feed labels are not authoritative when the affecting source
  text explicitly names a structural repeal of the exact affected unit. A
  `words omitted` / `word omitted` / `words repealed` row whose source says
  `omit subsection (N)` and whose metadata target is exactly that subsection
  lowers as `REPEAL` with
  `uk_effect_word_omission_structural_subsection_repeal_reclassified`; mismatch
  cases remain blocked text-patch failures rather than target hijacks.
- UK fragment parsing owns common instruction-text text-patch forms before any
  manual compile claim: quoted anchor-to-end substitutions without the word
  `words`, direct quoted word omissions such as `omit the "or" at the end`, and
  bounded definition-entry substitutions lower to typed text patches with rule
  IDs. Definition-entry substitution replaces the uniquely bounded definition
  entry; it must not delete a bare phrase occurrence or rewrite an ambiguous
  definition list.
- Explicit definition-list anchors are deterministic text-patch surfaces, not
  manual placement guesses. `before the definition of "X" insert ...` lowers to
  `TEXT_BEFORE_DEFINITION_X`; `in the definition of "D", after "A" insert "B"`
  lowers to `TEXT_IN_DEFINITION_D_AFTER_A` using an internal selector separator
  so the replay executor rewrites only the uniquely bounded definition entry.
  The same rule applies to interpretation lists drafted as `before/after the
  entry for "X" insert "Y"`, but only when the inserted payload itself is a
  definition entry (`means`, `includes`, `has the meaning`, `is to be
  construed`, etc.). Non-definition entity-list entries remain outside this
  lowering and must not be flattened into a generic text insertion.
  A before-definition anchor may match the first entry after an interpretation
  dash and a definition term followed by a qualifier comma or colon, such as
  `"X", in relation to ...`, because the source target is still the explicit
  definition term and replay inserts before the term boundary rather than
  guessing a placement from live text.
  After-definition replay also treats comma-separated interpretation lists as
  definition-entry boundaries when the named term resolves uniquely; this is a
  bounded definition-entry selector, not authority to append to the whole
  subsection tail.
  `after "A", in both places insert "B"` remains an all-occurrences text patch
  over the explicit target subtree. Ambiguous definition entries, missing
  anchors, and source text saying only `at the appropriate place(s)` are not
  covered by these rules and must remain candidate/manual-compile evidence.
- Definition-child parent context must not be lost because the child payload
  contains a quoted legal reference. If a child row says only `after "X" insert
  "..."` or `for "X" substitute "Y"` and the parent says `in the definition of
  "D", in paragraph (a)(ii)`, lowering scopes the patch to
  `TEXT_IN_DEFINITION_CHILD_PARAGRAPH_D` with the parent-supplied child label.
  Quoted payload references such as `in section 2A` are inserted text, not a
  competing target. The owned rules are
  `uk_effect_source_parent_definition_child_after_quoted_anchor_insert_text_patch`
  and `uk_effect_source_parent_definition_child_substitution_text_patch`.
  If a BlockAmendment child row says only `at the end there is inserted` and
  the parent supplies the same definition-child context, lowering uses
  `uk_effect_source_carried_definition_child_at_end_insert_text_patch` and a
  `TEXT_IN_DEFINITION_CHILD_PARAGRAPH_* AT_END` selector. This is a bounded
  text append to the source-owned definition child, not an address-only insert
  into a synthetic `section/paragraph/subparagraph` path that the enacted tree
  does not expose.
  Direct source text of the form `at the end of the definition of "D" insert
  "X"` lowers to `TEXT_IN_DEFINITION_D AT_END` with
  `uk_effect_in_definition_at_end_insert_text_patch`. Replay must resolve
  exactly one definition entry and insert before the next definition separator
  in comma/semicolon-separated interpretation lists; it must not append to the
  whole subsection tail when the definition boundary is ambiguous.
- UK quoted text-patch lowering also owns the common bounded occurrence
  variants when the source names an explicit affected target. `for "X", in
  each/both place(s) it occurs, substitute "Y"` and parenthesized variants such
  as `for "X" (in each place it appears) substitute "Y"` lower to
  all-occurrences text replacement; `for the first/second/... "X" substitute
  "Y"`, `for "X" in the first/second/... place it occurs substitute "Y"`, and
  `before "X", in the first/second/... place it occurs, insert "Y"`, and
  `after "X", in the first/second/... place it occurs, insert "Y"` lower with
  an explicit occurrence index. The equivalent prefixed form `after the
  first/second/... "X" insert "Y"` is the same bounded text-patch family.
  `for the words "X" in paragraph N substitute "Y"` lowers as
  `uk_effect_child_qualified_quoted_substitution_text_patch` when the effect
  feed already targets that child; the source child qualifier is preserved as
  evidence and does not authorize widening or rebinding the feed target.
  `omit the final "X"` lowers with the final-occurrence selector
  `occurrence=-1`, not all-occurrences deletion, so earlier conjunctions or
  repeated words remain intact.
  `for "X" (in the first and second places it appears) substitute "Y"` and
  `for "X", in the first two places where it occurs, substitute "Y"` lower to
  two explicit occurrence-indexed text patches, applied in descending
  occurrence order, so a third occurrence is preserved and the source does not
  silently become an all-occurrences substitution. Current first-two witness:
  `ukpga/1978/29` affected `s. 15(1)` by `asp/2014/9` `s. 63(3)(a)(i)`. A
  narrow parenthesized
  nested-quote source form such as
  `for "('X')" substitute "(a 'X')"` lowers as the exact punctuation/quote
  text patch named by the source. If the selected occurrence or quoted
  punctuation preimage is absent from the target surfaces,
  replay must emit the existing text-preimage drift/missing classification
  rather than inventing an anchor. A non-operative parenthetical source aside
  between a quoted `after "X"` anchor and `insert "Y"` is ignored for this
  lowering; it is evidence about the prior insertion, not part of the target
  preimage. `at the beginning insert "Y"` lowers to `TEXT_BEGINNING`, which may
  prepend only to the target node's own text and must not escalate into child or
  parent structure. `for the words after "X" substitute "Y"` lowers to
  `TEXT_AFTER_X_TO_END`: replay retains the explicit anchor and rewrites only
  the target node's own tail, or a uniquely matching descendant text node. It
  must not flatten a target subtree just to make the tail replacement apply.
  `omit the words after "X"` lowers to the same tail selector with an empty
  replacement under `uk_effect_after_anchor_to_end_omission_text_patch`; this
  is a bounded text deletion scoped to the effect-feed target, not permission
  to delete descendants or sibling structure. Current witness: `ukpga/1970/9`
  affected `s. 9(1)(b)` by `ukpga/2016/24 Sch. 1 para. 51(4)(a)`.
  `for the words before paragraph (a) substitute "Y"` lowers to
  `TEXT_BEFORE_CHILD_paragraph_a` and may replace only the explicit target
  node's own lead text when that target has exactly one direct child matching
  the cited child label.
  `at the end insert "Y"` and `insert at the end "Y"` lower with
  `uk_effect_at_end_text_insertion_patch` to an append patch (`TEXT_END`), not
  to a synthetic replace-from-empty-to-end selector. Replay appends to the
  target node's own text when present, or to the last text-bearing descendant
  when the target is a container, preserving existing text and children. It
  must not flatten the target subtree or replace the whole target with only
  the inserted tail.
  `omit the words from "X" to "Y"` lowers to `TEXT_FROM_X_TO_Y` deletion. When
  both bounded range anchors are found in the explicit target node's own text,
  replay rewrites that node text and preserves existing children. Only a
  genuinely cross-descendant bounded range may fall through to the destructive
  target-subtree collapse path. These rules do not cover table or cell targets,
  heading or cross-heading facets, definition-child deletion, or `appropriate
  place(s)` placement.
  Passive range repeals may qualify the end anchor by ordinal occurrence, e.g.
  `the words from "X" to "Y", where it first occurs, are repealed`; lowering
  records `end_occurrence` under
  `uk_effect_range_independent_end_occurrence_repeal_text_patch` so replay
  does not broaden the range to the wrong repeated end anchor. Current witness:
  `ukpga/1978/29` affected `Sch. 7 para. 6` by `asp/2005/13 s. 38(3)(c)`.

## UK Effect Ordering

- UK replay effect order is legal time plus affecting-source citation order:
  effective date, editorial modified date, affecting act id, natural
  `affecting_provisions`, then effect id as a final stable tie-breaker. Opaque
  effect ids are not legal order.
- When a same-date/same-affecting-act group is reordered by source citation,
  the pipeline emits nonblocking
  `uk_effect_source_provision_order_normalized` evidence with the original and
  ordered effect ids/provisions. Strict mode records this normalization because
  it is a deterministic source-order rule, not a target recovery.
- Parenthesized single-letter source labels such as `(c)` and `(d)` are
  alphabetic legal labels for ordering. They must not be Roman-normalized to
  100/500. Roman ordering is used for multi-letter Roman numerals and for
  nested `(i)` style labels after alphabetic parents.
- Schedule materialization may rank structural inserts before text edits so
  dependent target shapes exist before replay mutates them. Heading-facet
  patches normally stay early, but not when a same-effective-date/same-source
  structural insert or replacement has the same target path; in that case the
  structural op creates the heading carrier first. Current witness:
  `ukpga/2020/17` Schedule 6 paragraph 43A, inserted by `ukpga/2022/32` Sch.
  17 para. 1 and then amended by Sch. 17 para. 12(9)-(10).
- Same-target text patches may be reordered only by an exact quoted preimage
  chain inside the same effective-date bucket. If operation A replaces `old`
  with `middle` and operation B replaces `middle` with `new`, B depends on A
  even when opaque feed ids or modified-date ties put B first.
  - lowering emits `uk_effect_text_patch_preimage_chain_ordered` when it applies
    this deterministic order
  - if the chain is cyclic or non-unique, lowering emits blocking
    `uk_effect_text_patch_preimage_chain_ambiguous` and leaves source order
    intact
  - this rule does not do fuzzy numeric matching or cross-target inference
  - current witness: `asp/2000/2` section 4(1), where `ssi/2001/7` changes
    `£589,278,000` to `£626,571,000` before `ssi/2001/68` changes
    `£626,571,000` to `£626,568,000`
- Empty-effect-type rows must not infer structural repeal from temporary
  `shall have effect as if ... words were omitted` language.
  - this wording is an applicability/temporary modification surface, not a
    claim that the broad affected provision is repealed
  - lowering emits blocking
    `uk_effect_empty_type_as_if_words_omitted_rejected` rather than converting
    the broad affected provision to `REPEAL`
  - current witness: `asp/2000/1` / `ssi/2000/11 reg. 2`, which names
    `s. 11` but only states temporary treatment of words in `s. 11(9)`
- Source-backed inserted/replaced payload descendants need deterministic local
  identity when the source payload lacks descendant `eId`s.
  - lowering emits `uk_payload_descendant_eid_synthesis` for non-schedule
    payload descendants derived from the explicit target root
  - this is identity normalization only; it must not create or delete legal
    text and strict mode can block the synthesis when disabled
  - current witness: `asp/2000/1` / `asp/2010/13 s. 97(2)(b)`, where inserted
    `s. 11(5A)` owns child paragraphs `(a)` and `(b)` but the affecting source
    payload does not provide child EIDs
- Generic UK source container labels may be inferred from unambiguous source
  URI/id ordinals, but only in source statute parsing, not amendment payload
  parsing.
  - `part` means `part-1`; `part-n2` means `part-2`; likewise `schedule` and
    `schedule-n2`
  - source parsing emits `uk_container_number_inferred_from_source_uri`
  - replay/oracle EID comparison normalizes this source URI ordinal spelling
    drift without mutating replay state
  - current witness: `asp/2001/2`, where enacted XML uses generic visible
    `Part`/`SCHEDULE` labels with ids such as `part-n2` and `schedule-n2`,
    while the current oracle exposes numbered containers
- Payload-less source-owned structural repeals must carry the serialized UK
  lowered-operation witness sidecar in provenance, just like text rewrites and
  renumbers. Source-text-only authority mode reads this sidecar to distinguish
  a repeal proven by affecting-act text from metadata-only replay authority.
  Without it, deterministic source-table repeals are incorrectly rejected as
  non-source-text operations. Current witness: `asp/2001/8`, where source-owned
  repeal-table Part `1`/`2` rows must pass source-text-only replay authority.

## UK Target Shape Normalization

- A non-schedule affected-provision reference of the form `s. N(a)` names a
  direct section paragraph, not an alphabetic subsection. Lowering records
  `uk_effect_direct_section_paragraph_target_normalized` and emits
  `section:N/paragraph:a`; deeper references such as `s. N(a)(ii)` emit
  `section:N/paragraph:a/subparagraph:ii`. Witness:
  `asp/2001/2`, affected by `asp/2005/12 s. 51(2)(a)-(b)`, and
  `ukpga/2020/17` section 399(c)(ii)-(iv), affected by `ukpga/2022/32`
  section 124(8). Replay preserves this direct paragraph path instead of
  re-canonicalizing it into subsection/paragraph shape during target lookup.
  If that paragraph is not represented as an addressable XML carrier under the
  section, replay emits `uk_replay_direct_section_paragraph_carrier_gap`
  rather than falling back to a subsection or whole-section text patch. The
  exception is source-proven definition-child context: if the affecting source
  parent explicitly says that the paragraph is inside a named definition in the
  same section, lowering keeps that fact as a definition-child selector and
  records `uk_effect_source_parent_definition_child_target_refined`.
- Source wording such as `in sub-paragraph (2), paragraph (b) is repealed`
  is nested context, not a sibling list. Sibling expansion must not split it
  into `.../subparagraph:2` plus `.../item:b`; it must preserve the metadata
  target `.../subparagraph:2/item:b`. Witness: `asp/2000/1`, affected by
  `asp/2010/8 s. 118(8)(e)(i)`.
- Source wording such as `in subsection (5), at the beginning of paragraph (b)
  insert ...` is placement context, not a sibling target list. Text expansion
  must not synthesize bogus targets such as `section:22at/subsection:the/...`;
  the source-owned target remains the feed target. Witness: `asp/2000/1`,
  affected by `asp/2010/8 s. 118(5)`.
- Source wording such as `For sections 3 to 12 ... substitute— CHAPTER 1 ...`
  is a range-to-container substitution, not proof that an existing
  `part:2/chapter:1` node may be replaced or inserted blindly.
  - source pathology emits `range_to_container_target_unsupported`
  - lowering emits blocking `uk_effect_range_to_container_substitution_rejected`
    and does not replay the unsafe container replacement
  - the blocking lowering record carries typed frontier facts:
    `source_range_kind`, `source_range_start`, `source_range_end`,
    `source_range_section_count`, `source_range_sections`,
    `truncated_source_range_sections`, `target_container_ref`,
    `compiled_targets`, `payload_kinds`,
    `payload_roots` (including root label/eId and bounded direct-child
    summaries plus bounded descendant section labels), and
    `required_ownership`
  - manual-frontier classification emits
  `uk_manual_frontier_range_to_container_candidate`
  - suggested semantic-compile claim templates copy the bounded payload root and
    source/replacement-section evidence into the work item so a reviewer can
    validate the claimed replacement range without reverse-engineering the
    lowering record
  - a future replay implementation must own the replaced section range, the new
    container payload, and lineage/migration evidence before mutating the tree
  - current witness: `asp/2001/2`, affected by `asp/2019/17 s. 35(2)` and
    `s. 38(2)`
- Heading/title/sidenote suffixes are target facets, not child labels. The
  supported heading-facet rules record
  `uk_effect_heading_facet_word_patch_lowered` or
  `uk_effect_heading_facet_full_replacement_lowered` and emit a heading facet
  target such as `section:6/heading`, never
  `section:6/subsection:title/heading`. Witnesses: `asp/2000/6`, affected by
  `asp/2016/8 s. 3(3)`, and `asp/2000/11`, affected by `asp/2006/10 Sch. 6
  para. 9(2)(f)`. Mixed structural inserts such as `s. 207(3A) and heading`
  are a separate target-normalization family; they lower only the source-owned
  structural child and keep the heading facet unresolved.
- When a heading-facet target resolves to a real heading carrier but the quoted
  source preimage is absent from that heading text, replay emits
  `uk_replay_heading_text_preimage_gap` and leaves the heading unchanged. This
  is a text-surface gap, not permission to rewrite the body, widen to a sibling,
  or substitute by semantic synonym. Witness cluster: `asp/2000/7`, affected by
  `ssi/2013/197`, heading ops against sections 9 to 11.
- Insert-after/insert-before effects that lower as a text replacement preserve
  the anchor text inside the replacement. If the target resolves but that anchor
  is absent from the target subtree, replay emits
  `uk_replay_text_insert_anchor_preimage_gap` and does not insert by guessing a
  nearby location. Witnesses include `asp/2000/11`, affected by `asp/2010/13`
  `s. 106(6)(a)`, and `asp/2000/4`, affected by `ssi/2005/610 reg. 2`.
- Monetary amount substitutions are exact text-surface operations. If a target
  resolves but the quoted amount is absent, replay emits
  `uk_replay_text_monetary_amount_preimage_gap`; it does not substitute a nearby
  amount by numeric similarity or fiscal context. Witnesses include
  `asp/2000/2`, affected by `ssi/2001/68 art. 2(2)`, and `asp/2001/4`,
  affected by `asp/2003/6 s. 8(a)`. Row-level effect inspection classifies both
  as `uk_manual_frontier_text_patch_preimage_chain_gap`, because the source
  preimage is absent from the available enacted/current target surfaces. This
  differs from owned table-cell selectors, where the preimage belongs to a table
  cell and the current non-cell compare surface is classified separately.
- Parenthetical omission effects are exact text-surface operations. If the
  quoted parenthetical is absent from the resolved target, replay emits
  `uk_replay_text_parenthetical_omission_preimage_gap`; it does not delete a
  larger host phrase or infer an editorially equivalent omission. Witness:
  `asp/2000/1`, affected by `ukpga/2012/11 Sch. 3 para. 32`.
- Citation-list substitutions sometimes encounter replay/oracle surfaces where
  connector words between citation fragments are elided even though the
  alphanumeric citation sequence is visible. Replay emits
  `uk_replay_text_match_citation_connector_surface_gap` and blocks the fuzzy
  replacement rather than treating the connector-elided surface as an exact
  preimage. Witness: `asp/2001/2`, affected by `asp/2019/17 sch. para. 3(4)(a)`.
- Article-prefixed phrase substitutions are not lowered by matching only the
  post-article content word. If the selector is shaped like `an approved` and
  the target contains `approved` only in a different phrase shape, replay emits
  `uk_replay_text_match_article_phrase_surface_gap` and blocks. Witness:
  `asp/2000/4`, affected by `asp/2007/10 s. 60(1)(a)`.
- A section-body title substitution can expose a prior-source timeline gap
  rather than a text-surface family. Witness: `asp/2000/7`, affected by
  `ssi/2013/197 Sch. 2 para. 2(a)`, expects the prior `Public Standards
  Commissioner for Scotland` surface introduced by `asp/2010/11 Sch. 1 para. 1`.
  When the archived current affecting-source XML for that prior amendment
  extracts only a dot-leader/repealed shell, but the official enacted
  `/{affecting_act_id}/enacted/data.xml` extracts substantive text for the same
  affecting provision reference, the UK frontend may select the enacted source
  lane. This emits
  `uk_affecting_act_current_shell_enacted_source_selected` with current/enacted
  locators and text previews, and lowers the operation with authority layer
  `AFFECTING_ACT_ENACTED_TEXT`. Fallback is same-provision only; enacted text
  elsewhere in the affecting act is not enough. The `source_text_only` replay
  authority lane accepts this source layer because it is official affecting-act
  text selected by a witnessed acquisition rule, not metadata backfill.
- Some UK current affecting-source XML omits an extractable same-provision node
  even though official enacted XML still contains it. The frontend may select
  the enacted same-provision source lane when current extraction is missing and
  enacted extraction is substantive. This emits
  `uk_affecting_act_missing_current_enacted_source_selected`; it is source
  acquisition recovery only, not permission to invent lowering.
- UK effects metadata can name a schedule Part context even when descendant
  paragraph IDs omit the Part segment. The extractor may normalize
  `Sch. N Pt. P para. X` to `Sch. N para. X` only when the extracted paragraph
  has a matching Part ancestor in the source XML. This emits
  `uk_affecting_act_nonaddressable_schedule_part_context_ignored`; if the
  ancestor is absent or different, extraction stays unresolved.

## UK Metadata Renumbering

- Explicit UK effect metadata of the form `X renumbered as X(1)` lowers to a
  typed `RENUMBER` operation only when the destination is the source provision's
  own immediate descendant. The lowering emits
  `uk_effect_metadata_renumber_lowered` with source and destination targets.
- The metadata prefix `word(s) in X renumbered as Y` is source scope language,
  not a child label. Lowering strips `word(s) in` before target parsing so the
  source target remains `X`, while the destination still controls the new
  descendant label. Current witnesses include `ukpga/2020/17` Schedule 22
  paragraph 72(a), paragraph 43(a), and paragraph 54(a).
- If extracted operative text and effect metadata disagree on the new
  descendant label, source text controls. The rule
  `uk_effect_source_text_renumber_destination_corrected` applies only when the
  metadata row is already a narrow descendant renumber and source text says
  e.g. `become paragraph (a)` while metadata says destination `(b)`. Lowering
  records both the source-stated destination and the corrected metadata
  destination witness.
- Explicit UK effect metadata of the form `X(n) renumbered as X(m)` also lowers
  to typed `RENUMBER` when source and destination are same-parent, same-kind
  siblings and the labels differ. The lowering emits
  `uk_effect_metadata_sibling_renumber_lowered`.
- Replay materializes this narrow shape by preserving the source provision's
  identity as the parent, moving its current text and children into the new
  descendant, and assigning the descendant eId derived from the destination
  address. This supports later same-source text patches against the new
  descendant without treating the text patch as a target-resolution fallback.
- Destination collision checking for same-provision descendant renumbering is
  direct-child only. Broad recursive target resolution is intentionally not used
  here because a nested item such as roman `i` can normalize like `1`, but it is
  not a collision for `paragraph 12 becomes sub-paragraph (1)`.
- Same-parent sibling renumbering materializes only when the source exists and
  the destination is absent. Replay relabels the source node to the destination
  label, derives the destination eId, and reorders it under the same parent; it
  does not insert, replace, or repeal by text coincidence.
- Cross-container metadata renumbers such as `Sch. 22 para. 88 renumbered as
  Sch. 2 para. 88(1)` are not same-provision descendant wraps. They imply
  migration across top-level schedule containers plus descendant wrapping, and
  currently emit `uk_effect_metadata_cross_container_renumber_rejected` with
  source and destination targets. Manual-frontier triage classifies these as
  `uk_manual_frontier_cross_container_renumber_candidate`. They must remain
  blocking until LawVM owns cross-container lineage semantics.
- Other broader renumbers, moves, or destination collisions remain unsupported
  replay actions until LawVM owns their lineage semantics explicitly. They must
  not be coerced into inserts, replaces, or parent rewrites.

## UK Nested Target Splitting and Insert Anchors

- Adjacent parenthesized alphabetic labels are expanded as same-parent siblings
  only when they are a coherent sibling family. One-letter labels paired with
  two-letter labels must share the one-letter stem, so `(d)(da)` may expand as
  siblings but `(a)(zi)` remains a nested target. This prevents target
  smuggling from `...128(6)(a)(zi)` into sibling targets `...128(6)(a)` and
  `...128(6)(zi)`.
- Source text of the form `before sub-paragraph (i) insert ...` on a structural
  insert lowers with an insertion-anchor witness naming the following sibling
  eId. Replay inserts before that sibling when present. This is an explicit
  source anchor, not a label-sort fallback.
- If the following sibling cannot be found, replay falls back to the existing
  insertion-parent resolution and sorted insertion paths; the unresolved anchor
  shape should remain visible through the replay result or a later adjudication
  family rather than rewriting a parent or sibling.

## UK Oracle Identity Drift

- Official current XML can carry an `id`/`eId` whose parent path contradicts
  the element's physical XML ancestry. This is an oracle/source identity drift,
  not replay permission to retarget a source-backed amendment.
- The UK grafter records this as
  `uk_oracle_physical_parent_eid_drift_aligned` when the official EID and the
  physical ancestry-derived EID have the same non-empty section/article/rule/
  regulation root and leaf label but disagree on the intermediate parent path.
  The record carries
  `original_eid`, `physical_eid`, `xml_tag`, and `physical_path_key`; strict
  disposition is `block`, quirks disposition is `record`.
- Replay comparison may use the emitted alias only as a comparison
  normalization. It does not mutate the replay tree, alter target resolution,
  or change the official oracle EID set used as source evidence.
- Witness: `asp/2002/1` current XML physically places the inserted paragraph
  `aa` under `section-5-4`, while the child node's official ID says
  `section-5-1-aa`. The affecting source `asp/2006/14 s. 25` explicitly
  targets `section 5(4)(aa)`, so LawVM keeps the replay node
  `section-5-4-aa` and classifies the mismatch as oracle identity drift.
- The rule must not alias schedule paragraph wrapper drift merely because both
  EIDs lack a section/article root. UK schedule IDs such as
  `schedule-1-paragraph-12n3` versus `schedule-1-paragraph-12C-1` can encode
  wrapper/display-number differences that need separate schedule-aware
  comparison handling.
- A separate comparison-only source identity lane records
  `uk_oracle_visible_number_eid_alias_aligned` when a schedule paragraph EID
  uses an `n` placeholder but the same XML element's visible `Pnumber` supplies
  the leaf label, for example `schedule-2-paragraph-21n1` with visible number
  `21ZA`. Replay comparison may align this to
  `schedule-2-paragraph-21za`; replay target resolution and mutation semantics
  must not use the alias as permission to retarget an amendment.

## UK Current Projection Surfaces

- Some UK current XML surfaces for small amending Acts retain only the citation
  and commencement section while hiding spent amending provisions. This is a
  current projection/oracle-surface shape, not a replay instruction to delete
  source-backed amendment sections.
- The benchmark classifier may mark this as
  `spent_amending_act_current_projection` when the oracle EID set is a small
  single-root strict subset of the enacted EID set. This is non-core for replay
  fidelity scoring; replay still preserves source-backed enacted provisions
  unless a source effect repeals them.
- Witness: `asp/2002/2` current XML retains only section 3 and descendants.
  The effect feed repeals section 2, but it does not provide a source-backed
  repeal for section 1. LawVM therefore keeps section 1 in replay and treats
  the current XML's omission as current-projection evidence.
- UK XML `Text` fragment IDs such as `p00090` are transport anchors, not legal
  provision identities. Oracle EID extraction already excludes them; replay
  comparison normalization also drops them so score residuals stay about legal
  units rather than inline text spans.
- Some current XML surfaces are broad commencement projections rather than
  small spent-amending-act projections. If the full oracle EID set is a strict
  subset of replay and the independently computed commencement lens exactly
  agrees (`commenced_replay == commenced_oracle`), the benchmark/CLI may
  classify the row as `commencement_current_projection`. This is also
  comparison-only and non-core: replay does not delete replay-extra future or
  uncommenced structure merely to match the current XML view.
- Witness: `asc/2023/1` has 421 replay comparison EIDs and 284 current-oracle
  EIDs with no oracle-only residuals after comparison normalization. The
  commencement replay/oracle lens is 100%, so the low full-score row is a
  current projection surface, not a replay mutation gap.

## UK Source-Backed Renumber Residual Claims

- A current oracle can retain the pre-renumber EID while the effect feed and
  affecting source both prove an applied same-parent renumber. This is not a
  replay-bug proof by itself, because the comparison still needs source/oracle
  adjudication, but it should not be hidden under generic
  `uk_mixed_residual_eids`.
- UK bench residual-claim classification may refine such rows to
  `uk_source_backed_renumber_oracle_branch_mixed_residual_eids` when:
  - the comparison is core;
  - replay/oracle residual sets contain an oracle-only EID matching the
    lowering observation's `source_target`;
  - the lowering observation is one of
    `uk_effect_metadata_sibling_renumber_lowered` or
    `uk_effect_source_text_renumber_destination_corrected`.
- This classification remains `UNRESOLVED`; it is an evidence-routing label
  for applied source-backed lineage/oracle-incorporation drift, not permission
  to alter replay, suppress residual EIDs, or prove an official consolidation
  error automatically.
- Witness: `asc/2024/6` current source-first replay applies
  `asc/2025/3 Sch. 1 para. 62`, whose source says to omit `s. 16(8)` and
  renumber `s. 16(9)` as `s. 16(8)`. The current oracle still exposes
  `section-16-9`, so the residual claim is refined to the source-backed
  renumber oracle-branch family while staying unresolved.

## UK Residual Claim Evidence JSONL

- `lawvm uk-candidates --residual-claim-evidence-jsonl PATH` writes selected
  saved bench residual claims as `lawvm.uk_residual_claim_frontier.v1` JSONL
  work items. The export is an evidence/review surface only; it must not alter
  replay, candidate selection, or benchmark scoring.
- A residual claim row is reviewable when it has a non-empty claim kind other
  than `no_strong_claim`/`unknown_legacy_missing` and carries replay/oracle
  residual counts or a section-claim count. Empty saved rows are omitted so the
  file is a work queue, not a copy of the whole benchmark.
- Each work item carries the saved replay regime, replay-regime source claim,
  residual claim, source/oracle witness metadata, scores, comparison class, and
  a stable `uk-residual-claim-*` ID. `validator_status` starts as
  `not_validated`; downstream human/LLM review may classify the item, but the
  saved evidence row remains separate from replay execution.

## UK Manual-Frontier Validation

- `lawvm uk-manual-frontier-validate INPUT.jsonl` validates exported
  `lawvm.uk_manual_compile_frontier.v1` rows against the current archive-backed
  compiler. The validator re-summarizes each `(statute_id, effect_id)` and emits
  `lawvm.uk_manual_frontier_validation.v1` rows with old and current
  manual-frontier status/rule IDs.
- This is a stale-workqueue detector, not a replay shortcut. It must not mutate
  replay state, alter benchmark scoring, or reinterpret a blocked row as
  supported unless the current compiler already emits deterministic operations
  without blocking lowering.
- Validator statuses distinguish at least:
  `resolved_deterministic_supported`, `resolved_compiles_without_blocking_lowering`,
  `still_manual_frontier`, `still_blocked_without_manual_frontier_classification`,
  `effect_not_found`, and `input_error`.
- Missing keys and missing effect-feed rows are blocking validation findings.
  Rows still present in the manual frontier remain nonblocking diagnostic
  evidence because the original lowering/adjudication lane owns the legal
  block.
- `--validation-jsonl PATH` writes all validation findings. `--remaining-jsonl
  PATH` writes only still-live manual-frontier workqueue rows, preserving the
  original source witness row and attaching the current validation summary. The
  remaining file is a pruned agent work queue; it must not include rows already
  resolved by deterministic lowering.
- `--fail-on-stale` exits nonzero after printing/writing the report if any
  exported workqueue row is already deterministic or otherwise no longer live
  manual-frontier work. `--fail-on-validation-error` exits nonzero for malformed
  rows or effect IDs that no longer exist. `--fail-on-remaining` exits nonzero
  when any row remains live manual-frontier work, which is useful when a curated
  queue is expected to have been fully discharged. These flags are script guards
  only; they must not change validation classification.
- `--summary-only` suppresses per-row stdout output while preserving aggregate
  counts and any requested JSONL exports. It is intended for dashboards and
  automation over large work queues where the JSONL artifacts, not stdout,
  carry the detailed row evidence.
- Validator summaries include remaining/stale manual-rule count maps and
  current blocking-lowering count maps. This makes summary-only output a usable
  next-action selector: agents should pick from `remaining_manual_rule_counts`
  rather than scanning stale rows by hand.

## UK EID Lookup Scope Discipline

- Cached suffix EID lookup is a target-resolution accelerator only. A
  top-scoped target such as `section-2-9` may not be satisfied by an indexed
  suffix alias carried by a node outside the exact `section-2` subtree, even if
  an unrelated source EID happens to end in the same suffix.
- The suffix cache therefore verifies top-scoped hits against the exact
  top-scope node before returning them. If the indexed hit is outside the
  target's top scope, lookup falls back to the scoped recursive search or to a
  visible miss; it must not escape into another section/chapter/schedule branch.
- Regression witnesses:
  `test_executor_eid_search_does_not_escape_strict_top_scope_after_miss` and
  `test_executor_eid_search_does_not_escape_strict_top_scope_with_sequence_match`.

## UK Bench Text-Score Performance Lane

- UK bench EID score and replay diagnostics are the primary corpus-sweep
  measurements. Levenshtein text similarity is a diagnostic lane over common
  EIDs and can dominate wall time for very large Acts.
- `lawvm bench -j uk --no-text-scores` disables only the diagnostic text
  similarity lane. It leaves source loading, parsing, EID scoring, replay,
  commencement filtering, adjudications, residual claims, and phase timings
  active.
- Default behavior still computes text similarity. Short normalized text-pair
  ratios are cached in-process, bounded by combined text length, to avoid
  retaining large legal text blobs.
- Performance witness on `ukpga/2010/4` with `--replay --phase-timings
  --parallel 1`: default text scoring kept replay score at `96.9%` and wall
  time around `24.5s`; `--no-text-scores` kept replay score at `96.9%` and
  reduced wall time to about `16.9s`.
- Phase timing names must describe measured work. Enacted EID scoring is
  recorded separately from enacted text similarity, and replay residual
  classification is recorded separately from replay text similarity. Runs with
  `--no-text-scores` should not report `text_score_*` phases.

## UK Residual Candidate Root Evidence

- `lawvm uk-candidates --residual-claim-evidence-jsonl` emits diagnostic-only
  residual candidate evidence. It must not change replay, lowering, target
  resolution, or oracle classification.
- Residual evidence includes root-level candidate hit counts, side counts
  (`replayed_only`, `oracle_only`, `both`), source-pathology counts, compare
  shape counts, structural/non-structural counts, and manual-frontier rule
  counts. It also carries bounded replay-adjudication kind/bucket counts and
  samples when available, so a residual work item preserves the adjudication
  witness that selected its claim kind. These fields are next-action selectors
  for large residual frontiers.
- The same residual-candidate count families are aggregated into the
  `uk-candidates` JSON summary so corpus-level runs can rank likely replay
  work without parsing each emitted row.
- `residual_candidate_root_samples` gives representative effect rows for the
  highest-count residual roots. The sample selector prefers structural,
  replay-applicable, source-clean candidates with compiled operations. This
  prevents the work queue from depending on the arbitrary first few candidate
  effects when a statute has many overlapping residual branches.
- Residual samples carry compact target-presence counts. For insertion effects,
  `oracle_targets_absent` is evidence for a possible oracle freshness/editorial
  gap or a temporal applicability mismatch; it is not by itself proof of a
  replay defect.
- Residual summaries also include action-family counts and
  target-presence/action cross-counts. A concentration such as
  `oracle_targets_absent:insert` should be triaged as an oracle/current-surface
  question before changing replay, because the source-backed operation may be
  valid while the archived current oracle has not incorporated it.
- If a residual root is `oracle_targets_absent:insert`, live official current
  XML may be used as a freshness witness before replay work. On 2026-05-25,
  live `https://www.legislation.gov.uk/ukpga/2020/17/data.xml` contained
  `section-264-3A`, `section-264-3B`, `section-277-3A`, and `section-277-3B`
  with the same effect keys that were absent from the local archived current
  oracle. The archived current witness was `8126157` bytes with SHA-256 prefix
  `f960734f`, while the live current witness was `8315694` bytes with SHA-256
  prefix `90f6de85`, so that cluster is local archive staleness unless a
  refreshed archive still disagrees. The bounded refresh command for this case
  is `uv run python scripts/acquire_uk_corpus.py refresh --statute ukpga/2020/17
  --force-refresh`; it refreshes the target statute's current XML and effects
  feed without scanning the whole corpus.
- The evidence is not proof that a candidate caused the residual. It only says
  a source-backed compiled effect overlaps a residual root. Agents must inspect
  the effect row and phase-local diagnostics before changing replay behavior.
- Example current witness: refreshed `ukpga/2020/17` residual evidence shows
  `59` residual-overlap candidate effects and `74` ops, with root concentration
  around `section-264`, `section-277`, and `section-350`. The `section-350`
  triage demonstrates why root evidence is only a selector: one inspected
  `6C/6D` row lowers to operations but is not replay-applicable and is absent
  from the current oracle, so it is not itself a replay fix target.

## UK Source-Carried Structured Tail Substitution

- Rule `uk_effect_source_carried_structured_tail_substitution_lowered` handles
  the narrow source-owned form `in subsection (n), for the words from "X" to
  the end of the subsection substitute - a ... b ...`, and the corresponding
  schedule form `in paragraph N, in sub-paragraph (m), for the words from "X"
  to the end, substitute - a ... b ...`.
- Lowering is permitted only when the effect target is the same subsection
  or subparagraph named in source and each replacement child label is visibly
  present in the source row. It emits child `REPLACE` operations for the
  labelled paragraphs/items instead of flattening the payload into parent text.
- A schedule child row may omit the paragraph number only when the official
  affected target supplies a schedule/paragraph/subparagraph address and the
  source row itself is visibly a labelled child row naming the same
  subparagraph. The row label in text must match both the XML structural
  number and the final child label in the affecting provision reference.
  The lowering observation then records
  `source_scope_context=explicit_source_with_effect_target_context`; it must
  not consult live-tree uniqueness or infer a paragraph from sibling source
  rows silently.
- The same effect-target context rule applies when the affecting provision is
  a numbered source row such as `s. 17(8)`: the XML row label, the source-text
  row label, and the final parenthesized affecting-provision label must all
  match before the omitted schedule paragraph context may be admitted.
- Schedule subparagraph lowering only splits contiguous top-level alpha labels
  (`a`, `b`, ...), including an explicit `and`/`or` connector before the next
  expected alpha label. Nested roman lists inside an item remain payload text
  of that item until a deeper structural claim owns them.
- Replay recovery remains visible through
  `uk_replay_source_carried_structured_tail_substitution_recovered`. For
  `from "X" to the end` forms it trims the parent with
  `TEXT_FROM_X_TO_END`; older `following/after "X"` forms continue to trim with
  `TEXT_AFTER_X_TO_END`.
- Strict mode must treat the recovery as blockable. Quirks mode may apply it
  only with the replay adjudication carrying the source anchor, trim selector,
  payload kind/label, and parent-tail trimming evidence.

## UK Compound Lettered Text Patch Instructions

- Rule `uk_effect_compound_lettered_text_patch_instruction` handles one source
  paragraph that carries multiple lettered word-level instructions, such as
  `a for "X" there is substituted "Y", and b after "Z" there is inserted "W"`.
- The parser must not admit a quote-spanning synthetic preimage across the
  sibling boundary. It first discards the false `for ... there is inserted ...`
  capture if its preimage contains embedded quote marks, then marks the real
  sibling fragments with the compound rule.
- Lowering emits one bounded `TEXT_REPLACE` operation per marked fragment,
  preserving the shared affected target and attaching the compound rule in
  the text-rewrite witness. It must not collapse the row to the first fragment
  or compile the entire source paragraph as one broad text replacement.
- This is a parser/lowering family, not a replay recovery. Strict mode should
  be able to block any future compound form whose fragments cannot be split
  into independently owned text patches.

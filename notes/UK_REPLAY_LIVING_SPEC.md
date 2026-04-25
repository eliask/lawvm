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
- deep roman descendant labels preserve roman suffixes in fallback IDs
  - `section-88-3c-b-ii` and `schedule-7a-paragraph-9-2-b-ii` are correct local fallback shapes
  - replay must not emit numeric fake tails like `...-1` / `...-2` where oracle uses `...-i` / `...-ii`
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
  - `territorial_extension_oracle_gap`

UK work should continue to add typed classes only when a deterministic archive-backed pattern repeats.

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

Current schedule-reference invariant:

- bare schedule references like `Sch 4 para. 2` must normalize exactly like `Sch. 4 para. 2`
  - they are schedule-rooted targets, not `section:sch/...`
  - current example: `ukpga/2002/21` `Sch 4 para. 2` / `Sch 4 para. 8`
  - fixing that normalization moved `ukpga/2002/21` from `99.6%` to `99.7%`
    by removing the false `schedule-4-paragraph-2/8` replay tail

Current text-span invariant:

- `for the words from the beginning to "X" substitute "Y"` is a valid text-span
  replacement form and compiles to `TEXT_FROM__TO_X`
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

# Conformance Corpus

Status: living spec, intentionally partial.

Purpose:

- define exemplar replay families that future implementations must reproduce
- pair prose rules with statute-level evidence
- keep the cleanroom/compiler target grounded in concrete source artifacts

This file is not a full corpus inventory.
It is the beginning of a normative exemplar set.

Related docs:

- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [FINLAND_ARCHITECTURAL_COHERENCE.md](FINLAND_ARCHITECTURAL_COHERENCE.md)
- [REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md)

## 1. Intended Structure

Each exemplar should eventually capture:

- statute id
- amendment id
- problem family
- clause shape
- payload shape
- intended canonical ops
- intended final materialization
- proof/evidence expectation
- validation commands

For now, some entries are lighter than others.

### 1.1 Recommended v0 fixture shape

The v0 corpus should assert by phase summary, not by dumping full internal
Python objects.

Recommended shape:

- one YAML file per exemplar case
- explicit phase blocks
  - `clause`
  - `payload`
  - `elaboration`
  - `canonical`
  - `replay`
  - `evidence`
- each phase block optional
- `must` means normative test failure
- `should` means advisory/non-blocking

### 1.2 Phase summary principle

Each phase should assert against a small summary artifact, not raw in-memory
objects.

Examples:

- clause summary
  - flat op codes
  - tags
- payload summary
  - tags
  - pathology codes
  - key flags
- elaboration summary
  - slot links
  - slot assignment summary
  - decision tags
  - pathology codes
- canonical summary
  - simplified action/target pairs
- replay summary
  - after score
  - focus text
  - warnings
- evidence summary
  - primary tier
  - claim kinds
  - source pathology codes

## 2. Finland Exemplars

### 2.1 `1988/161` / `2008/732` / `14 §`

Family:

- sparse multi-subsection payload
- subsection intro + item replacement alignment

Observed source shape:

- amendment section contains two subsections
- each subsection has a distinct intro
- each subsection updates only `1)` explicitly
- omitted trailing content must be preserved from the live statute

Failure that existed:

- subsection `2 mom johd` fell back to subsection 1’s intro because intro ops
  were not consuming the same elaborated subsection slot as sibling item ops

Required behavior:

- subsection intro replacements and sibling item replacements must map to the
  same subsection slot
- resulting text must preserve the distinct second intro:
  - `Lupaa ei myöskään tarvita ...`

Expected validation:

```bash
uv run lawvm trace-section 1988/161 --mode legal_pit --source 2008/732 --section '14 §'
```

Expected outcome:

- `After score: 100.0%`

### 2.2 `2009/1672` / `2024/1116` / `7 luvun 14 b §`

Family:

- whole-section repeal placeholder
- post-materialization address preservation

Observed source shape:

- repeal ops compile correctly for:
  - `7 luku 14 a §`
  - `7 luku 14 b §`

Failure that existed:

- canonical timeline already had separate active repeal placeholders for `14a`
  and `14b`
- Finland repeal-range consolidation merged them into one synthetic
  `14 a–14 b § on kumottu ...` node
- this destroyed addressability of `14 b §`

Required behavior:

- separate same-number suffix sections like `14a` and `14b` must remain
  individually addressable
- section-level range consolidation may still merge cross-number runs like
  `49 a–50 §`

Expected validation:

```bash
uv run lawvm trace-section 2009/1672 --mode finlex_oracle --source 2024/1116 --section '14 b §'
```

Expected outcome:

- `After score: 100.0%`
- visible placeholder:
  - `14 b § on kumottu L:lla 30.12.2024/1116.`

Canonical conformance lesson:

- `14 a §` and `14 b §` may be presentation-neighbors
- they are not one canonical replay slot

### 2.3 `1991/827` / `2012/751`

Family:

- qualified heading list continuation
- mixed target-list PEG coverage

Observed shape:

- johto includes coordinated heading and section targets with language qualifiers

Failure that existed:

- PEG stopped early and truncated the target list badly
- large parts of the amendment family never compiled into ops

Required behavior:

- qualified heading list continuation must keep parsing through the full target
  sequence
- replay must emit the later section replaces instead of silently truncating

Validation examples:

```bash
uv run lawvm inspect-amendment 1991/827 --mode legal_pit --source 2012/751
uv run lawvm trace-section 1991/827 --mode legal_pit --source 2012/751 --section '16 §'
```

Expected outcome:

- the later section operations appear in inspect output
- `16 §` improves to near-oracle alignment rather than staying in the low tail

### 2.4 `1982/710`

Family:

- temporary-law expiry coherence
- same-numbered stale scaffold consumption

Observed shape:

- temporary chapter/section families expire
- later durable changes must survive expiry correctly

Required behavior:

- replacing temporary content must not accidentally make it permanent
- later durable child updates must survive expiry of the temporary scaffold
- same-numbered insertions must consume stale non-base scaffolds instead of
  duplicating them

### 2.5 `1992/1702` / `1995/1599` / `30 §`

Family:

- bounded subsection replace
- single-slot bind with remaining replay divergence

Observed shape:

- amendment bundle is structurally simple:
  - `REPLACE 30 § 2 mom`
  - one bound sparse slot label `2`
  - no leftover slots
  - no frontend elaboration observations
  - no source pathology on the group itself

Current replay/evidence state:

- live trace still diverges after the blamed amendment:
  - `before_vs_oracle: 0.7917`
  - `after_vs_oracle: 0.7787`
- but evidence now explicitly demotes this family to
  `blamed_source_payload_prefers_replay`
- the published section-fragment payload matches replay materially better than
  the oracle even though the blamed step slightly worsens total section
  similarity

Why this exemplar matters:

- it is a good evidence-spec exemplar for narrow section-fragment payloads
- it proves the demotion rule cannot depend only on whole-section
  `improved_or_equal`
- it is no longer the next active frontend target

Expected conformance focus:

- elaboration summary must show the successful slot bind
- replay/evidence summary must explain why the clean bind is source-backed

### 2.6 Finland replay-regression micro-suite proposal

This is the smallest stable Finland replay-regression watchlist for the current
bad set. It is a proposal for bench review, not a new fixture family or a new
runner.

Anchors:

- `2000/252 §3` - sparse subsection payload binding and later-moment source
  order
- `1981/555 §11` - consolidation split that must keep the 4th and 5th moments
  distinct
- `2006/766` - sparse insert before terminal `voimaantulo`
- `2014/1429` - inserted chapter topology

Optional tail candidates:

- `2013/492`
- `1994/1217`

Existing executable anchors:

- `tests/test_payload_normalize.py::test_build_subsection_slot_assignment_shares_plain_and_item_ops_on_same_moment`
- `tests/test_timeline_properties.py::test_sparse_suffix_subsection_replaces_keep_source_order_in_2000_252`
- `tests/test_materialization_invariants.py::Test1981_555Section11Split::test_1981_555_section_11_materializes_fourth_moment`
- `tests/test_merge.py::test_merge_section_with_nested_subsection_omission_preserves_master_tail`
- `tests/test_replay_products.py::test_replay_xml_preserves_sparse_insert_before_terminal_voimaantulo_for_2006_766`
- `tests/test_replay_products.py::test_replay_xml_preserves_inserted_chapter_topology_for_2014_1429`

Use this set as the default replay-regression micro-suite when triaging the
current Finland bench tail.
  divergence rather than a replay bug

Validation examples:

```bash
uv run lawvm evidence 1982/710 --mode legal_pit --json
```

Expected outcome:

- no longer an honest replay-bug exemplar
- replay-side residue shrinks to editorial or unresolved residue instead of
  obvious stale duplication

### 2.6 `1992/1702` / `1996/761` / `33 §`

Family:

- late section identity/path drift
- bounded subsection replacement with later final-section mismatch

Observed shape:

- amendment bundle is structurally simple:
  - `REPLACE 33 § 2 mom`
  - one bound sparse slot label `2`
  - no leftover slots
  - no frontend elaboration observations
  - no source pathology on the group itself

Current replay state:

- the blamed amendment does change the section:
  - `before_vs_oracle: 0.3057`
  - `after_vs_oracle: 0.3041`
- final evidence still keeps `chapter:8/section:33` under
  `replay_divergence`
- the final oracle section under `33 §` belongs to a different later section
  family than the local blamed amendment surface suggests

Why this exemplar matters:

- it is the cleaner next active target after `30 §` was demoted
- it suggests the remaining gap is not sparse-slot binding
- it points toward:
  - section identity / path drift
  - late materialization / disappearance
  - renumber-family handling across later replay

Expected conformance focus:

- elaboration summary should stay boring and narrow
- evidence/review should make clear that the surviving divergence is about
  section-number continuity versus later section identity, not local slot
  ownership

### 2.7 `1984/718` / `1993/688` / `35 §`

Family:

- source-backed whole-section replace
- proof/evidence demotion for oracle-only residual text

Observed shape:

- the blamed amendment compiles as a whole-section replace
- live trace improves the section substantially across the blamed amendment
- the published amendment section payload matches replay text better than the
  consolidated oracle text
- the residual oracle-only sentence is not supported by the source payload

Failure to avoid:

- classifying this shape as a clean replay bug simply because the oracle still
  contains extra text after the blamed amendment

Required behavior:

- section-bisect / trace support should preserve whether the blamed source
  payload materially matches replay better than the oracle
- proof claims should demote such sections from `PROVED_REPLAY_BUG` to
  `PROVED_SOURCE_PATHOLOGY`
- if the same section was already bad before any amendment-induced drop, that
  stronger `preexisting_baseline_residue` support may still keep the statute in
  `UNRESOLVED` rather than making the whole statute a source-pathology exemplar

Validation examples:

```bash
uv run lawvm trace-section 1984/718 --mode legal_pit --source 1993/688 --section '35 §'
uv run lawvm evidence 1984/718 --mode legal_pit --json
```

Expected outcome:

- `35 §` improves materially across `1993/688`
- section-bisect support shows both:
  - `blame_source_improved_or_equal`
  - `blame_payload_prefers_replay`
- the section is not treated as a clean replay-bug exemplar

### 2.8 `1961/404` / `2005/821`

Family:

- whole-johtolause continuation after same-section heading/language qualifier
- frontend miss that previously degraded into uncovered-body recovery

Observed shape:

- the johtolause contains:
  - `19 §:n 1 momentti sekä pykälän edellä olevan väliotsikon ruotsinkielinen sanamuoto, 20 § ...`
  - later `lakiin uusi 11 a, 13 a ja 16 a–16 e § sekä kunkin edelle uusi väliotsikko`
- the isolated insertion snippet parses correctly
- the full johtolause previously stopped after `19 §:n 1 momentti`

Failure that existed:

- PEG did not treat bare genitive `pykälän edellä olevan väliotsikon ...` as a
  same-section heading qualifier
- full extraction stopped early
- `20`, `21`, `24` and `16b–16d` were missing from canonical frontend ops
- replay then relied on uncovered section insert fallback from chapter payload

Required behavior:

- same-section continuation logic should skip both:
  - `sen edellä oleva väliotsikko ...`
  - `pykälän edellä oleva väliotsikko ...`
- the rest of the target list must remain parseable
- `16b–16d` should appear as first-class insert ops during amendment
  compilation, not only as uncovered-body recovery

Validation examples:

```bash
uv run python -m lawvm.finland.johtolause.test_peg_curated
uv run lawvm inspect-amendment 1961/404 --mode legal_pit --source 2005/821
```

Expected outcome:

- curated PEG case:
  - `muutetaan 19 §:n 1 momentti sekä pykälän edellä olevan väliotsikon ruotsinkielinen sanamuoto, 20 § ja sen edellä oleva väliotsikko, 21 ja 24 §`
  - yields `M P 19 1`, `M P 20`, `M P 21`, `M P 24`
- live inspect output includes:
  - `INSERT 16b §`
  - `INSERT 16c §`
  - `INSERT 16d §`

### 2.9 `1961/404` / `2005/821` / `2 luku` and `5 a luku`

Family:

- malformed container-membership overbundling
- chapter payload contains section bodies that are also separately targeted

Observed shape:

- after the frontend fix, the amendment now emits first-class standalone
  targets for inserted/replaced sections
- the published chapter payloads still bundle section children that do not
  belong in the live container payload for this canonical op grouping
- for `2 luku`, the pruned bundled children are:
  - `11a`, `13a`, `16a`, `16b`, `16c`, `16d`, `16e`
- for `5 a luku`, the bundled chapter payload includes the full section family
  that is also emitted as standalone insert targets

Failure to avoid:

- treating this as a remaining replay/frontend bug and trying to keep both:
  - the chapter payload children
  - the standalone section targets
- that would duplicate container membership and corrupt canonical ops

Required behavior:

- chapter payload normalization must prune standalone-target children that are
  bundled into the container payload but do not belong to the live container
  membership for that grouped op
- the compiler should keep the chapter-level canonical op:
  - `REPLACE 2 luku otsikko`
  - `INSERT 5a luku`
- and emit typed source-pathology evidence:
  - `CONTAINER_MEMBERSHIP_MISMATCH`
  - with explicit `pruned_sections`

Validation examples:

```bash
uv run lawvm inspect-amendment 1961/404 --mode legal_pit --source 2005/821
```

Expected outcome:

- `2 luku` reports:
  - `CONTAINER_MEMBERSHIP_MISMATCH`
  - `pruned_sections = ['11a', '13a', '16a', '16b', '16c', '16d', '16e']`
- `5a luku` reports:
  - `CONTAINER_MEMBERSHIP_MISMATCH`
  - the bundled `54–64b §` family in `pruned_sections`
- the container groups keep only the intended chapter-level ops instead of
  duplicating standalone section membership

### 2.10 `1990/1295` / `1993/805` / `35 §`

Family:

- sparse subsection slot alignment
- adjacent replace/insert moments
- local payload numbering vs live moment ownership
- base-source missing chapter span

Observed shape:

- the base `1990/1295` source jumps from `6 luku / 32 §` directly to
  `11 luku`
- the tail of `32 §` carries a textual placeholder:
  - `Puuttuu luvut 7-11`
- the compiled ops are:
  - `REPLACE 35 § 2 mom`
  - `INSERT 35 § 3 mom`
- the amendment body for `35 §` carries:
  - a leading section-level omission
  - then payload subsections labelled `1` and `2`

Current live nuance:

- this is not yet a pure slot-map bug in the full pipeline
- before `1993/805`, the relevant chapter span is already absent in the base
  replay state
- the replay probe before `1993/805` does not expose `35 §` as a straightforward
  present section in `replay_fold_state`
- so the family currently sits at the boundary between:
  - base source incompleteness
  - sparse slot ownership
  - local payload label normalization
  - section presence/materialization

Required behavior:

- the compiler must classify or repair the missing base chapter span explicitly
  rather than pretending the family is only a local slot-assignment problem
- the elaborated group should make live moment ownership explicit before apply
- if payload subsections are using local numbering after a leading omission,
  replay must not let those local labels corrupt live moment semantics
- the section should remain materially present and individually queryable across
  the blamed step

Validation examples:

```bash
uv run lawvm inspect-amendment 1990/1295 --mode legal_pit --source 1993/805
uv run lawvm trace-section 1990/1295 --mode legal_pit --source 1993/805 --section '35 §'
```

Expected target direction:

- textual base gaps like `Puuttuu luvut 7-11` become explicit source/base-gap
  pathology or seeding inputs
- slot ownership is explained by an elaborated-group product, not by late
  first-unmatched-subsection fallback once the containing section is real
- the family becomes either:
  - a resolved sparse-slot/materialization case
  - or an explicitly classified non-frontend failure mode

### 2.11 `1984/719` / `1996/295` / `78 §` and `1987/1094` / `97 §`

Family:

- evidence-side structural drift, not replay execution
- same-section unmatched oracle subsection fragments
- same-chapter oracle presentation-range section topology

Observed shape:

- `1996/295` compiles cleanly for the relevant family:
  - `REPEAL 78 § 2 mom`
  - `REPLACE 78 § 1 mom`
  - `REPEAL 79 § 2 mom`
  - `REPLACE 79 § 1 mom`
- live replay preserves later siblings:
  - `78 §` stays at subsection labels `1,4,5`
  - `79 §` stays at subsection labels `1,3`
- but the oracle for `78 §` still carries an extra subsection fragment already
  unmatched before the blamed amendment:
  - `Jos henkilö on suorittanut korkeakoulututkinnon...`
- `1987/1094 / 97 §` compiles as a clean `REPLACE 97 §`
- replay has exact `chapter:11/section:97`
- oracle instead carries:
  - `chapter:11/section:96a–97`

Required behavior:

- do not treat these as honest frontend/replay debt just because same-number
  section comparison looks bad
- evidence should demote:
  - `1996/295 / 78 §` to `preexisting_same_section_structure_drift`
  - `1987/1094 / 97 §` to `same_chapter_oracle_range_drift`
- the compiler should keep replay semantics unchanged here
- the queue should not keep `1984/719` as the next active Finland family after
  these demotions

Validation examples:

```bash
uv run lawvm evidence 1984/719 --mode legal_pit --json
uv run lawvm inspect-amendment 1984/719 --mode legal_pit --source 1996/295 --json
uv run lawvm inspect-amendment 1984/719 --mode legal_pit --source 1987/1094 --json
```

Expected evidence outcome:

- `78 §` selects `preexisting_same_section_structure_drift`
- `79 §` selects `oracle_section_stale`
- `97 §` selects `same_chapter_oracle_range_drift`
- the statute should no longer emit a residual `replay_divergence` proof claim

## 3. What This Corpus Is For

This corpus should be used for:

- replay regression tests
- architecture migrations
- cleanroom reimplementation targets
- evidence/proof contract checks

This corpus should not be treated as:

- the full low-tail inventory
- a complete list of all jurisdictional shapes
- a replacement for statute-scale replay testing

## 4. Expansion Rules

Add a new exemplar when at least one is true:

- it exposed a repeated missing abstraction
- it forced a replay invariant change
- it changed the intended canonical-op contract
- it distinguishes replay bugs from source/oracle faults in a reusable way

Do not add entries that are just one-off clutter with no architectural value.

## 5. Old-Clause PEG Sentinel

- `1901/15-001 / 1987/411`
- use this as a bounded old-syntax frontend sentinel, not as a mainline modern
  Finland priority family
- current moderate-effort expectation:
  - Roman-numeral part refs like `III ja V osa` must parse as structural part
    targets
  - old demonstrative provenance like
    `niihin myöhemmin tehtyine muutoksineen` must not kill the target list or
    the next verb group
  - mixed old clause `kumotaan 55 §, III ja V osa` should yield:
    - `REPEAL 55 §`
    - `REPEAL III osa`
    - `REPEAL V osa`
  - the bounded real-clause bridge
    `kumotaan ... 55§, III ja V osa niihin myöhemmin tehtyine muutoksineen, muutetaan I osa`
    should also keep the next verb group alive and yield:
    - `REPEAL 55 §`
    - `REPEAL III osa`
    - `REPEAL V osa`
    - `REPLACE I osa`
- the later old same-verb continuation syntax in the full `1987/411`
  johtolause is still intentionally outside the current normative target
  surface

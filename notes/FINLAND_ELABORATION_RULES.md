# Finland Elaboration Rules

Status: living spec, intentionally partial.

Purpose:

- define what elaboration is allowed to do after syntax/payload parsing
- constrain Finland-specific recovery so it does not collapse into replay-time
  heuristic soup

Related docs:

- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)

## 1. Elaboration Boundary

Elaboration sits between:

- syntax frontend
- payload-shape extraction
- canonical op compilation

Elaboration may inspect:

- typed clause structure
- typed payload structure
- live target tree

Elaboration may not rely on:

- free-form late substring filtering inside replay
- silent fallback to arbitrary first/last siblings when a typed slot should be
  determined
- presentation-driven merges that destroy canonical addressability

## 2. Allowed Elaboration Families

### 2.1 Sparse subsection slot alignment

Allowed:

- map sparse amendment subsections onto live target moments when the clause and
  payload clearly target those moments
- ensure sibling intro/item/plain updates for the same moment share the same
  subsection slot

Not allowed:

- global fallback to the first amendment subsection just because a `johd` node
  did not get typed slot information

### 2.2 Omission-aware preservation

Allowed:

- preserve unmodified trailing material from the live target when omission
  markers and payload shape justify it
- attach terminal section omission only to the justified tail subsection

Not allowed:

- duplicating preserved tails into multiple sibling payloads
- treating omission as permission to rebuild arbitrary missing structure

### 2.3 Broad-to-narrow rewrites

Allowed:

- rewrite broad section-level changes into narrower row/item/subsection changes
  when:
  - payload shape is narrow
  - live target structure matches
  - the rewrite is typed and auditable

Not allowed:

- broad-to-narrow rewrites based only on superficial text resemblance
- target reinterpretation after canonical ops already exist

### 2.4 Chapter/section scope recovery

Allowed:

- preserve explicit chapter bindings from johto when the clause structure
  actually supports them
- use live tree evidence to avoid stripping real scope

Not allowed:

- stripping chapter scope simply because a bare section label looks unique in
  one local path

### 2.5 Named row-table elaboration

Allowed:

- use typed named-target hints to rewrite section/table payloads into row-level
  ops
- use inflection-aware row-name matching where Finnish morphology clearly
  relates source and live row labels

Not allowed:

- raw marker-word blacklists as the primary target-resolution mechanism

## 3. Sparse Multi-Subsection Elaboration Model

The main Finland sparse family should be modeled as a constrained monotone
alignment problem, not as ad hoc `op -> subsection` fallback.

The right units are:

- `MomentIntent`
  - one logical changed moment in the live target section
  - groups all sibling facets for that moment
    - whole-subsection replace
    - intro replace
    - item replace
    - item insert
- `PayloadSlot`
  - one logical sparse payload slot after local payload normalization
- `Gap`
  - an explicit omission-derived constraint between slots

This gives one key invariant:

- single-consumption
  - every payload fragment belongs to at most one logical slot
  - every logical slot belongs to at most one changed moment

That is the main defense against duplicating one subsection's intro into later
subsection slots.

### 3.1 Build moment intents first

Elaboration should group by owning subsection first, then by facet.

So these all belong to one logical moment:

- `REPLACE 14 § 2 mom`
- `REPLACE 14 § 2 mom johd`
- `REPLACE 14 § 2 mom 3 kohta`

Important consequence:

- intro/item/plain ops for one moment must not compete independently for
  different payload slots

### 3.2 Normalize payload slots before alignment

The old Finland fold cluster should converge on one typed step:

- `normalize_local_sparse_slots(...)`

Its job is:

- decide which raw wrappers/fragments form one logical slot
- classify omissions as either:
  - `IntraSlotGap`
  - `InterSlotGap`
- preserve leading unlabeled fragments as typed local facts rather than
  immediately counting them as standalone changed moments

This is the point where a split intro/list body or content-only continuation
can be attached locally without yet deciding final live coordinates.

Leading unlabeled fragments do not increment slot count by themselves unless a
separate rule explicitly proves they are standalone slots.

### 3.3 Solve a monotone alignment against live state

After local slot normalization, elaboration should align:

- ordered `MomentIntent`s
- ordered `PayloadSlot`s
- `InterSlotGap`s
- live subsection tree

using a monotone constrained alignment.

This can be:

- explicit DP
- bounded backtracking
- another deterministic solver

The important point is the contract, not the specific algorithm.

### 3.4 Hard alignment constraints

At minimum:

- mapping order must remain monotone
- one slot cannot satisfy two distinct moments
- one moment cannot consume two slots unless they were already normalized into
  one logical slot
- exact clause coordinates must be respected
- exact payload labels must be respected unless the payload is provably using a
  local dense numbering convention
- `InterSlotGap` means the two surrounding slots cannot collapse onto one
  adjacent live boundary if that would erase a required untouched live sibling
- dense-offset assignment must not run while unresolved leading fragments or
  unclassified slot-local gaps remain
- fallback positional zip must not run while slot uncertainty remains non-zero

### 3.5 Evidence order

Elaboration should prefer a lexicographic evidence order instead of a fuzzy
score soup:

1. exact explicit target coordinate match
2. exact payload label match
3. exact item/row anchor match inside the slot
4. typed shift-pair relation
5. constant-offset local numbering
6. positional zip against explicit target sequence

If there is no unique best assignment under that order:

- stop and surface ambiguity
- do not pretend the slot ownership is known

### 3.5.a Consecutive replace/insert pair

The active `1990/1295 / 1993/805 / 35 §` family should be modeled as a typed
adjacent shift pair:

- `REPLACE N mom`
- `INSERT N+1 mom`

with sparse payload slots solved only after local slot normalization.

The rule is:

1. build `MomentIntent`s first
2. normalize raw payload children into logical `PayloadSlot`s
3. if exactly two ordered logical slots remain, with no stronger exact-label
   contradiction and no unresolved earlier gap, bind:
   - first logical slot -> `REPLACE N`
   - second logical slot -> `INSERT N+1`

This is not a generic zip-by-order rule. It is an explicit typed relation
between adjacent live moments.

Important consequence:

- moment ownership beats naive child index
- a leading unlabeled fragment may become part of the first logical slot
- it should not be treated as a raw standalone "subsection 2" merely because
  the first op targets moment 2

### 3.6 Densify only after assignment

Untouched live text may be preserved only inside the resolved owning moment.

Allowed:

- preserve live tail inside the resolved subsection when omission and slot shape
  justify it

Forbidden:

- borrowing text from neighboring live subsections to complete a slot
- copying one subsection intro into another subsection's payload

## 4. Practical Guardrails For The Current Sparse Family

Until a fuller elaborated-group contract lands, the following should hold:

1. exact labels beat shift-pair rules
2. shift-pair rules beat dense-offset inference
3. dense-offset inference beats positional fallback
4. insert may not skip an unresolved earlier slot while a preceding replace
   remains unresolved
5. if no unique assignment survives those constraints, emit typed ambiguity
   rather than guessed slot ownership

## 5. Escalation To Source Pathology

Elaboration should stop and emit source-pathology/adjudication signals when:

- the source payload loses too much structure to justify a unique mapping
- multiple live targets remain equally plausible
- the body payload does not actually support the blamed replacement semantics
- a supposed replacement source only supports a repeal for the section

The system should prefer:

- explicit pathology / unresolved classification

over:

- false replay certainty

Two practical cases:

- chapter/container payload overbundles section bodies that are also emitted as
  standalone targets
- sparse payload shape leaves multiple equally plausible slot assignments

In both cases the correct response is typed pathology or unresolved evidence,
not replay-time guesswork.

## 6. Replay Must Not Re-Elaborate

Once elaboration has produced canonical ops, replay should mostly:

- apply
- validate
- surface invariants

Replay should not still be deciding:

- which subsection a `johd` belongs to
- whether a section placeholder should remain individually addressable
- whether a clause really targeted rows instead of a whole section

## 6. Current Validated Rules

### Rule A: subsection intro slot sharing

Validated by:

- `1988/161` / `2008/732` / `14 §`

Rule:

- a subsection intro replacement belongs to the same elaborated subsection slot
  as its sibling payload-bearing updates for that target moment

### Rule A2: subsection-scoped intro address must remain explicit

Validated by:

- curated PEG family around
  `1 momentin johdantokappaleen ruotsinkielinen sanamuoto`

Rule:

- once the frontend already knows an intro target belongs to a specific
  subsection, later flat op representations must not erase that subsection
  scope

### Rule B: same-number suffix repeals stay individually addressable

Validated by:

- `2009/1672` / `2024/1116` / `7 luvun 14 b §`

Rule:

- presentation-level repeal normalization must not merge `14a` + `14b` into one
  synthetic section address

### Rule C: sparse subsection elaboration is a distinct typed phase

Validated by:

- `1988/161` / `2008/732` / `14 §`
- current `SubsectionSlotMap` and `_elaborate_sparse_subsection_payload(...)`
  extraction in
  [payload_normalize.py](/home/elias/c/civos/book/LawVM/src/lawvm/finland/payload_normalize.py)

Rule:

- sparse subsection mapping may remain implementation-backed for now
- but it must stay behind an explicit elaboration boundary rather than leaking
  raw `id(op) -> subsection` recovery into replay/apply/debug surfaces

### Rule D: container overbundling is source pathology, not replay meaning

Validated by:

- `1961/404` / `2005/821`

Rule:

- if a chapter/container payload bundles section children that are also emitted
  as standalone targets for that amendment grouping, normalization may prune the
  bundled children and emit `CONTAINER_MEMBERSHIP_MISMATCH`
- replay must not keep both the container children and the standalone targets

### Rule E: sparse slot assignment must prefer moment ownership over naive index

Validated by:

- `1990/1295` / `1993/805` / `35 §` as the current active bug family

Rule:

- when one group contains `REPLACE N mom` and `INSERT N+1 mom`, slot assignment
  must not drift backward and bind the replace to subsection `N-1` merely
  because the payload starts with unlabeled leading text
- the owning logical moment is the unit of alignment, not the first unmatched
  subsection wrapper by index

## 7. Immediate Next Rules To Specify

The next likely rule families to promote here are:

- sparse multi-subsection omission merge rules
- table-row elaboration contracts
- chapter-scoped repeal placeholder rules

Those should be added only after each family is stabilized by code and
conformance examples.

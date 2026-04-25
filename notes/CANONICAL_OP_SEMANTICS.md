# Canonical Op Semantics

Status: living spec, intentionally incomplete.

Purpose:

- define the intended semantics of canonical LawVM operations
- separate frontend/elaboration meaning recovery from replay execution
- give future AI or cleanroom implementations a stable contract that is better
  than "copy current code"

This document is normative where it states invariants and replay contracts.
It is provisional where it lists unresolved Finland-specific elaboration
questions.

Related design docs:

- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [FINLAND_ARCHITECTURAL_COHERENCE.md](FINLAND_ARCHITECTURAL_COHERENCE.md)
- [LAWVM_COMPILER_DIFFICULTY.md](LAWVM_COMPILER_DIFFICULTY.md)
- [REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md)

## 1. Role Of Canonical Ops

Canonical ops are the boundary between:

- frontend and elaboration
- replay execution

By the time an operation is canonical, replay should not still be inferring:

- clause structure
- conjunction attachment
- target family
- broad-to-narrow rewrite intent
- whether a sparse payload belongs to subsection 1 or subsection 2

Canonical ops are therefore an execution contract, not a free-form hint.

## 2. Canonical Op Model

At minimum, a canonical op must determine:

- action
  - `replace`
  - `insert`
  - `repeal`
  - `text_replace`
  - future families like renumber/move only when semantics are explicit
- target address
  - chapter / section / subsection / paragraph / special target
- effective source metadata
  - enacted
  - effective
  - optional expiry
  - source statute id
- payload
  - explicit IR subtree for `replace` and `insert`
  - `None` for tombstone-style `repeal`
  - typed replacement payload for `text_replace`

Frontend-only ambiguity must not leak past this boundary as raw text markers or
late string matching.

## 3. Action Semantics

### 3.1 `replace`

Meaning:

- replace the content at the target address with the provided payload

Constraints:

- target address must be specific enough that replay is not guessing among
  multiple siblings
- default canonical `replace` is not an upsert
- if the target does not yet exist, `replace` may degrade to insert-like
  behavior only when that is an explicit supported replay rule
- `replace` must not silently duplicate surviving siblings or descendants

Timeline meaning:

- a `replace` emits a new version for that address
- absent explicit expiry, a `replace` inherits the active temporary expiry only
  when preserving existing temporary semantics is the sound interpretation

### 3.2 `insert`

Meaning:

- add a new provision or child provision at the target address

Constraints:

- insertion ordering must be deterministic
- inserting into an already occupied exact slot must not create duplicate live
  siblings
- `insert` may consume only a non-substantive occupant at the same exact slot
- if the target is really a consumed stale scaffold or tombstone at that exact
  slot, replay may replace that occupant instead of duplicating it

### 3.3 `repeal`

Meaning:

- the targeted provision ceases to be in force as a substantive node

Two execution forms are allowed:

- tombstone semantics in timelines
- placeholder materialization where the jurisdiction/oracle convention wants a
  visible `on kumottu` node

Important:

- visible repeal placeholders are presentation-bearing semantic artifacts
  and must preserve addressability where the system expects section-level access

### 3.4 `text_replace`

Meaning:

- perform a constrained textual correction on an already targeted subtree

This family should remain narrow.
It is not a license for replay to become a generic text editor.

## 4. Address Semantics

Canonical addresses are semantic addresses, not just labels found in source.

Examples:

- `chapter:7/section:14b`
- `section:5/subsection:2`
- `section:14/subsection:2/special:johd`

Requirements:

- address labels must already reflect any chapter qualification the frontend
  or elaboration established
- address normalization must not collapse distinct legal addresses into one
  synthetic label if later reasoning expects individual access

Examples from current Finland behavior:

- `14a` and `14b` may be visually collapsed in some oracle renderings, but the
  canonical address space must still be able to represent them separately
- subsection intro targets must consume the same subsection slot as sibling
  payload-bearing item replacements when the frontend has already determined
  that they belong to the same moment

### 4.1 Exact slot identity

Replay uniqueness is defined only at the level of exact slots.

An exact slot is:

- parent exact address
- node kind
- exact normalized label

Examples:

- `chapter:7/section:14b`
- `section:5/subsection:2`
- `chapter:5a`

Exact slot identity controls:

- duplication
- occupancy
- `replace`
- scaffold/tombstone consumption

The unqualified phrase `same-number` should be avoided in normative specs.

### 4.2 Ordered sibling family

An ordered sibling family is all siblings of the same kind under the same
parent, ordered by the jurisdiction's canonical comparator.

Examples:

- `14`, `14a`, `14b`, `15`
- `5a luku`, `5b luku`

This family is used for:

- deterministic insertion position
- adjacency
- range eligibility checks

It does not determine slot uniqueness.

### 4.3 Stem family

A stem family is a suffix-aware subset of an ordered sibling family that shares
a common anchoring stem.

Examples:

- `14`, `14a`, `14b`
- `5`, `5a`, `5b`

This family is useful for:

- insertion anchoring
- suffix-aware ordering
- presentation hints

It is never sufficient to justify slot consumption.

### 4.4 Presentation range family

A presentation range family is a rendering-only grouping of contiguous exact
slots.

Examples:

- `49 a-50 § on kumottu`
- `2-5 momentit on kumottu`

This exists only for display/materialization. It must never redefine canonical
address identity.

## 5. Occupancy Model

Canonical replay must distinguish structural occupancy from substantive legal
content.

For each exact slot, the live state is one of:

### 5.1 Absent

No node occupies the exact slot.

### 5.2 Substantive occupant

A live node occupies the exact slot and carries substantive legal content.

### 5.3 Tombstone occupant

A non-substantive occupant exists at the exact slot and explicitly records
repeal.

A tombstone preserves addressability but is not substantive content.

### 5.4 Scaffold occupant

A non-substantive occupant exists at the exact slot for structural reasons,
without current substantive legal force.

A tombstone is a special scaffold with repeal meaning.

## 6. Action Semantics Against Occupancy

### 6.1 `insert`

`insert` is valid when:

- the exact slot is absent
- or the exact slot is occupied only by a tombstone or scaffold

In the second case, `insert` consumes that non-substantive occupant at the same
exact slot.

`insert` is invalid when:

- the exact slot already has a substantive occupant

### 6.2 `replace`

`replace` is valid when:

- the exact slot has a substantive occupant
- or the exact slot has a tombstone/scaffold occupant

`replace` is invalid by default when:

- the exact slot is absent

Jurisdiction-specific "replace into absent slot" behavior must be explicit and
non-default.

### 6.3 `repeal`

`repeal` terminates substantive occupancy of the exact slot.

Replay may realize this as:

- structural removal
- or tombstone replacement

depending on jurisdiction and materialization policy.

## 7. Replay Invariants

Replay is allowed to execute canonical ops.
Replay is not allowed to silently reinterpret them into unrelated meaning.

Detailed rollout and failure categories live in
[REPLAY_INVARIANTS_AND_FAILURE_MODEL.md](REPLAY_INVARIANTS_AND_FAILURE_MODEL.md).

Current invariant direction:

1. No silent duplication
- same-numbered sibling content must not be duplicated unless the op family
  explicitly requires coexistence

2. No impossible tree shapes
- duplicate same-labeled siblings under a node are an invariant violation
  unless a typed exception exists

3. No hidden fallback parsing
- replay should not rediscover johto structure from raw text after canonical ops
  already exist

4. Stable addressability
- presentation normalizers must not destroy individual legal addresses when
  later tooling expects to find them

5. Expiry coherence
- replacing temporary content must not accidentally make it permanent
- later durable changes under temporary scaffolds must survive expiry correctly

## 8. Placeholder Semantics

Placeholders are not merely cosmetic.

LawVM currently needs visible placeholder nodes for at least:

- repealed sections
- repealed subsections
- repealed items in some rendering paths

Rules:

- placeholder nodes must carry explicit marker attrs like
  `lawvm_repeal_placeholder`
- same-day ties prefer substantive content over a later placeholder when both
  claim the same address and date
- placeholder consolidation is allowed only when it does not destroy canonical
  addressability required by replay, timeline queries, trace tools, or tests

Current Finland policy:

- contiguous subsection placeholders may collapse into range text like
  `2–5 momentit on kumottu`
- section placeholder consolidation is allowed for cross-number runs like
  `49 a–50 §`
- section placeholder consolidation is not allowed to merge same-number suffix
  siblings like `14 a §` and `14 b §` into one synthetic address

## 9. Frontend / Elaboration Responsibilities

Canonical ops may rely on elaboration, but only before replay.

Allowed before canonicalization:

- sparse payload slot mapping
- row-table elaboration
- omission interpretation
- live-state-dependent alignment
- narrow broad-to-narrow rewrites

Not allowed after canonicalization:

- substring blacklists that decide target meaning
- global fallback to the first subsection when a typed slot should exist
- collapsing multiple legal addresses into one because a presentation form looks
  prettier

## 10. Known Current Exemplars

### 8.1 `1988/161` / `2008/732` / `14 §`

Lesson:

- intro replacements are canonical subsection-scoped operations
- if they fail to share the same subsection slot as the sibling item replace,
  replay corrupts meaning by borrowing the wrong intro

Canonical consequence:

- `special:johd` subsection ops must consume the same elaborated subsection slot
  as sibling payload-bearing ops for that moment

### 8.2 `2009/1672` / `2024/1116` / `7 luvun 14 b §`

Lesson:

- canonical op and timeline may already be correct while post-materialization
  presentation logic still destroys addressability

Canonical consequence:

- visual range consolidation must remain subordinate to address semantics

## 11. Non-Goals

This document does not yet specify:

- the full Finland clause AST
- the full Finland payload IR
- every legal effect family in every jurisdiction
- formal proof semantics

Those belong in follow-on docs.

## 10. Next Spec Dependencies

The next living specs that should refine this one are:

- `CONFORMANCE_CORPUS.md`
- `FINLAND_ELABORATION_RULES.md`
- `FINLAND_PAYLOAD_IR_SPEC.md`
- `FINLAND_CLAUSE_AST_SPEC.md`

This document should stay short enough that implementers can actually use it
as an execution contract rather than a narrative essay.

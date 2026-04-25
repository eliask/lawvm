# Finland Sparse Subsection Slot Spec

Status: living spec, intentionally partial.
Kind: normative.

Purpose:

- define the slot model for Finland sparse subsection payloads
- make sparse slot ownership explicit before replay/apply

Related docs:

- [FINLAND_ELABORATION_RULES.md](FINLAND_ELABORATION_RULES.md)
- [FINLAND_ELABORATED_GROUP_SPEC.md](FINLAND_ELABORATED_GROUP_SPEC.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)

## 1. Core Rule

Sparse slot assignment is a typed elaboration problem.

It is not:

- a replay-time heuristic
- a blind zip between target moments and raw amendment subsections

## 2. Main Units

### 2.1 `MomentIntent`

One logical changed live moment.

It groups all sibling facets for that moment:

- whole-subsection replace
- intro replace
- item replace
- item insert

### 2.2 `PayloadSlot`

One logical sparse payload slot after local normalization.

It may contain:

- an explicit numeric label
- a local dense numeric label
- no explicit label
- preserved prefix fragments
- preserved suffix fragments

### 2.3 `Gap`

A typed omission-derived boundary.

Important distinction:

- `IntraSlotGap`
  - omission inside one logical changed moment
- `InterSlotGap`
  - omission between two logical changed moments

## 3. Assignment Order

Sparse slot assignment should proceed in this order:

1. build `MomentIntent`s
2. normalize raw payload children into logical `PayloadSlot`s
3. classify gaps
4. solve monotone alignment
5. densify preserved live tails only inside the resolved owning moment

## 4. Evidence Order

Assignment should prefer a lexicographic order:

1. exact clause coordinate match
2. exact payload label match
3. exact item/row anchor match
4. typed shift-pair relation
5. constant-offset local numbering
6. positional fallback

If there is no unique best assignment:

- emit typed ambiguity
- do not guess

## 5. Leading Unlabeled Fragments

Leading unlabeled fragments do not increment slot count by themselves.

They are first-class local facts that may later become:

- part of the first logical slot
- or unresolved ambiguity

They should not immediately be treated as standalone live moments.

## 6. Adjacent Replace/Insert Pair

The active sparse family:

- `REPLACE N mom`
- `INSERT N+1 mom`

deserves its own typed rule.

If local normalization yields two ordered logical slots and no stronger
contradictory evidence, bind:

- first slot -> `REPLACE N`
- second slot -> `INSERT N+1`

This is a logical-moment rule, not a raw-child-index rule.

## 7. Guardrails

At minimum:

- dense-offset assignment may not run while unresolved leading fragments remain
- fallback positional zip may not run while slot uncertainty remains non-zero
- insert may not skip an unresolved earlier slot while a preceding replace
  remains unresolved
- one logical slot may not satisfy two distinct logical moments

## 8. Current Proving Families

Validated or active families:

- `1988/161` / `2008/732` / `14 §`
  - intro/item sharing inside sparse subsection alignment
- `1990/1295` / `1993/805` / `35 §`
  - adjacent replace/insert drift case

These should remain the anchor cases for future slot-model refactors.

## 9. Current Executed Slice

The current implementation now preserves two first-class sparse slot outputs:

- `sparse_slot_bindings`
  - assigned logical moment -> payload-slot ownership rows
- `unassigned_sparse_payload_slots`
  - leftover payload slots with no assigned owning moment

That is still not the full target slot/elaborated-group model, but it is a
real move away from slot ownership being trapped only in `SubsectionSlotMap`.

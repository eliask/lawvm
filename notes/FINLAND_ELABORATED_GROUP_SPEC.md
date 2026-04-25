# Finland Elaborated Group Spec

Status: living spec, intentionally partial.
Kind: normative.

Purpose:

- define the contract between Finland payload normalization/elaboration and
  replay apply
- stop late replay helpers from rediscovering sparse group meaning from raw
  payload fragments

Related docs:

- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [FINLAND_PAYLOAD_IR_SPEC.md](FINLAND_PAYLOAD_IR_SPEC.md)
- [FINLAND_ELABORATION_RULES.md](FINLAND_ELABORATION_RULES.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)

## 1. Core Rule

Replay/apply should consume an already elaborated group product.

It should not have to rediscover:

- which sparse payload slot belongs to which logical changed moment
- which omission is intra-slot vs inter-slot
- which payload fragments were discarded, preserved, or left unresolved

## 2. Group Product

An elaborated Finland group should carry at least:

- target scope
  - target kind
  - normalized target label
  - optional chapter scope
- prepared payload root
- canonical group ops
- elaborated slot bindings
- source pathologies
- adjudications / reasoning observations

The current implementation approximates parts of this with:

- `GroupPayloadNormalizationResult`
- `SparseSubsectionElaborationResult`
- `SubsectionSlotMap`
- `ElaborationObservation`

but those are still narrower than the target contract.

## 3. Minimum Fields

A stable elaborated-group contract should expose:

- `muutos_ir`
  - prepared/elaborated payload root
- `group_ops`
  - canonical ops after group-local rewrites and drops
- `slot_bindings`
  - logical moment to payload-slot ownership
- `leftovers`
  - payload fragments not consumed by any logical moment
- `source_pathologies`
- `observations`
  - typed reasoning facts emitted during elaboration

## 4. Slot Binding Semantics

The group product should bind by logical changed moment, not by raw child
position.

That means:

- sibling intro/item/plain ops for one moment share one logical slot
- local dense numbering may be preserved as payload evidence
- but the owning live moment must already be explicit before apply

Apply should not need to infer:

- whether payload subsection `1` really belongs to live moment `2`
- whether a leading unlabeled fragment increments slot count

## 5. Leftovers And Unresolved Facts

An elaborated group must be allowed to say:

- this payload fragment was consumed
- this payload fragment was preserved as local slot prefix/tail
- this payload fragment was dropped as unsupported
- this payload fragment remains unresolved

That is preferable to silently losing those facts before evidence sees them.

## 6. Relationship To Canonical Ops

Canonical ops remain the replay execution contract.

The elaborated group is the final frontend/elaboration product that justifies
those ops.

So:

- canonical ops answer "what should replay do?"
- elaborated groups answer "why are these the correct ops for this source
  payload and live target state?"

## 7. Near-Term Migration Target

The next practical target is:

- replace `SubsectionSlotMap` as the only sparse-elaboration carrier
- introduce an elaborated-group object that also records:
  - logical slots
  - ambiguity
  - unresolved gaps
  - assignment observations

Current executed slice:

- `GroupPayloadNormalizationResult` and `SparseSubsectionElaborationResult`
  now carry `elaboration_observations`
- these are still thin observations, not yet a full compiler observation schema,
  but they are real typed frontend outputs rather than replay-side inference
- current emitted families include:
  - sparse omission alignment
  - sparse split across consecutive replaces
  - sparse item-drop due to missing amendment body
  - container payload pruning under standalone section targets
- sparse elaboration results now also carry
  `unassigned_sparse_payload_slots`, so leftover sparse subsection payload slots
  survive as typed frontend output instead of disappearing behind the final
  `SubsectionSlotMap`
- sparse elaboration now also carries `sparse_slot_bindings`, giving one typed
  row per assigned logical moment -> payload-slot ownership instead of leaving
  slot ownership implicit in the old `SubsectionSlotMap` alone
- sparse/group normalization results now also carry a typed `slot_assignment`
  object, so slot map, binding rows, and leftover slot labels can travel as
  one frontend product instead of only as parallel fields
- downstream Finland consumers now start reading that carrier directly:
  `grafter.py` and `inspect_amendment.py` use `slot_assignment` as the first
  source for subsection mapping, binding rows, and leftover slot labels
- `inspect-amendment` now surfaces those leftover slot labels directly, so the
  new elaborated-group field is visible in the existing debug workflow
- `inspect-amendment` now also surfaces those slot-binding rows directly, so
  assigned sparse slot ownership is inspectable without reconstructing it from
  raw payload children
- `inspect-amendment` now exposes those observations directly so the
  elaboration product is inspectable in the existing debug workflow
- replay metadata now preserves them too, so the observation stream is no
  longer trapped inside `normalize_group_payload(...)`
- `compile_fi(...)` now also translates replay-carried elaboration
  observations into generic `frontend_elaboration_observation`
  adjudications, so they surface through existing compile-result and
  reporting paths without yet being treated as evidence-tier conclusions

The active proving family is:

- `1990/1295` / `1993/805` / `35 §`

That family should be solved behind this boundary rather than by adding more
late apply-time interpretation.

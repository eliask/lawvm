# Finland Payload IR Spec

Status: living spec, intentionally partial.

Purpose:

- define the intended intermediate representation for Finland amendment body
  payloads before canonical op compilation
- preserve source shape honestly enough that elaboration can be typed

Related docs:

- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [FINLAND_ELABORATION_RULES.md](FINLAND_ELABORATION_RULES.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)

## 1. Design Goal

Payload IR should preserve enough source structure that elaboration does not
need to rediscover meaning from flattened text.

The failure mode to avoid is:

- flatten payload too early
- then infer subsection/item/row structure from punctuation and sibling order

## 2. Minimum Required Shape

Payload IR must preserve, when present:

- section wrappers
- subsection wrappers
- intro/content distinction
- numbered paragraph children
- omission markers
- heading nodes
- row-like structures that are still visibly row-like
- malformed or incomplete source shape as explicit suspicious structure, not as
  fake clean structure

## 3. Surface Shape vs Elaborated Meaning

Payload IR should be split conceptually into two layers even if the current
implementation still uses `IRNode`-backed adapters.

### 3.1 Surface payload shape

This layer answers:

- what the source artifact actually contained
- in what order
- under what explicit wrappers or markers

It owns:

- exact observed subsection/section wrappers
- exact omission positions
- content-only blobs that have not yet been attached to a legal coordinate
- wrapper state
  - exact
  - flattened
  - malformed
- observed labels as source facts, not yet as resolved legal coordinates

Important rule:

- observed marker is not resolved coordinate

Examples:

- observed `2 momentti` wrapper means the source exposed a local marker
- it does not by itself prove that the payload belongs to live subsection `2`
- a flattened content blob after an omission remains an unattached fragment
  until elaboration assigns it

### 3.2 Elaborated payload meaning

This layer answers:

- what the surface payload most defensibly means once clause AST and live
  target state are consulted

It owns:

- sparse subsection slot alignment
- intro/item/plain content grouping inside one logical moment
- whether an omission is an inter-slot gap or an intra-slot preserved tail
- row/item narrowing against live structure
- source-pathology escalation when a unique mapping is not justified

The current implementation bridge is:

- surface shape is still mostly `IRNode`
- elaborated meaning is partially carried by
  - `SparseSubsectionElaborationResult`
  - `SubsectionSlotMap`
  - `GroupPayloadNormalizationResult`

That is not yet the final spec shape, but it is the correct conceptual split.

## 4. Typed Payload Shape Sketch

The target should not be another untyped `IRNode(kind=...)` tree alone.
`IRNode` remains useful as:

- raw extraction substrate
- replay tree representation

But the payload spec needs typed wrappers around it so uncertainty remains
explicit.

A good minimal target is:

- `PayloadSurface`
  - source ref
  - diagnostics
  - one root shape
- `SectionSurface`
  - observed section marker
  - coverage
    - whole
    - sparse
    - mixed
    - unknown
  - wrapper state
  - ordered members
- `SectionMember`
  - `SubsectionFrag`
  - `OmissionFrag`
  - `UnattachedFrag`
  - `OpaqueFrag`
- `BodySurface`
  - `PlainBody`
  - `IntroListBody`
  - `ParagraphListBody`
  - `TableBody`
  - `CompositeBody`

This is enough to express:

- preserved source order
- explicit omissions
- local list/table shapes
- malformed wrapper loss without pretending the structure was clean

## 5. Important Payload Families

### 3.1 Whole-section payload

Represents:

- a section-level amendment body

Must preserve:

- section heading
- subsection sequence
- content-only section bodies
- nested paragraph/item children

### 3.2 Sparse subsection payload

Represents:

- a section body where only some moments/items are explicit
- omission markers may indicate preserved live tails or internal gaps

Must preserve:

- local subsection wrappers
- exact intro/content split inside each subsection
- omission positions
- sibling order exactly as source serialized it

This family is central for:

- `1988/161` / `2008/732` / `14 §`
- `2000/252`
- `2002/885`

### 3.3 List-shaped subsection payload

Represents:

- one subsection whose visible legal content is mostly in numbered paragraph
  children

Must preserve:

- intro text
- paragraph labels
- unlabeled trailing content
- embedded continuation structures

### 3.4 Content-only subsection payload

Represents:

- a subsection where the source body does not preserve a richer inner structure

Rules:

- keep it as content-only
- do not invent paragraph children early
- any later promotion into item/paragraph structure must be an explicit
  elaboration step

### 3.5 Named row-table payload

Represents:

- section-level changes that really carry row/table semantics

Must preserve:

- row labels or names
- row ordering
- whether the source body was table-like or only content-like

This family should eventually connect cleanly to typed row-table elaboration,
not ad hoc string filtering.

## 6. Omission Semantics In Payload IR

Omission markers must remain first-class in payload IR.

They may later support different elaboration meanings:

- preserve unchanged suffix from the live target
- indicate an internal gap between explicit fragments
- mark a malformed/ambiguous source serialization

Payload IR itself must not decide all of those meanings.
It must preserve the raw omission placement faithfully.

## 7. Preservation Rules

The following are normative preservation rules for payload extraction.

1. Observed marker is not resolved coordinate.
- do not treat bare observed `2` as live subsection `2`

2. Omission stays first-class.
- extraction must not silently reinterpret omission as
  - preserve tail
  - skip one sibling
  - preserve remainder

3. Unattached fragments stay unattached.
- content-only continuation text remains its own payload fragment until an
  elaboration rule attaches it with evidence

4. Recovered wrappers must be marked recovered.
- if structure is reconstructed from flattened source text, that reconstructed
  structure must not masquerade as exact source shape

5. Broad-to-narrow rewrites are not payload extraction.
- row/item/subsection narrowing belongs to elaboration

## 8. Slot-Carrying Payload Shapes

One current architectural target is explicit subsection-slot carrying payload
shape.

Why:

- intro replacements, item replacements, and plain subsection replacements for
  the same moment should share a typed slot before replay

Current validated consequence:

- `special:johd` payloads cannot be left outside slot mapping and then recovered
  later by global first-subsection fallback

Current implementation bridge:

- [payload_normalize.py](../src/lawvm/finland/payload_normalize.py)
  now exposes `SubsectionSlotMap` as an explicit slot-assignment object
- this is not yet the final payload IR; it is an intermediate refactor step that
  names the elaboration boundary and removes some raw `id(op) -> subsection`
  plumbing

## 9. Relationship To Canonical Ops

Payload IR is not yet canonical execution content.

Payload IR may still be:

- sparse
- omission-marked
- locally numbered
- table-like rather than section-like

Canonical ops should only be produced after elaboration determines:

- target slot
- target family
- whether broad payload should stay broad or narrow into row/item/subsection ops

## 10. Pathology Surfacing

Payload IR should surface suspicious cases rather than silently smoothing them:

- destructive shape loss
- payload absent for blamed replacement
- unsupported row-table collapse
- malformed wrapper loss

These should remain available to:

- elaboration
- evidence/proof generation
- operator diagnostics

## 11. Current Implementation Direction

The current high-value migration rule is:

- move more logic from `_build_subsection_override_map(...)` into earlier typed
  sparse elaboration

That means:

- slot assignment should be consuming typed payload observations
- replay/apply should not still be recovering payload ownership

Recent Finland families that justify this direction:

- `1988/161` / `2008/732` / `14 §`
- `1990/1295` / `1993/805` / `35 §`
- `1961/404` / `2005/821` chapter payload overbundling

## 12. Near-Term Spec Additions

The next payload IR details worth specifying explicitly are:

- subsection-slot IR shape
- omission-bracketed sibling subsection bundles
- row-table payload hint objects
- payload pathology tags

This document should stay implementation-oriented and example-backed as those
families are stabilized.

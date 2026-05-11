# Finland Frontend / Elaboration Architecture

This document states the intended target architecture for the Finland replay
frontend. It is the design target going forward.

Companion specs:

- [SPEC_INDEX.md](SPEC_INDEX.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)
- [CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md)
- [FINLAND_CLAUSE_AST_SPEC.md](FINLAND_CLAUSE_AST_SPEC.md)
- [FINLAND_ELABORATION_RULES.md](FINLAND_ELABORATION_RULES.md)
- [FINLAND_PAYLOAD_IR_SPEC.md](FINLAND_PAYLOAD_IR_SPEC.md)
- [SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md](SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md)

The short version is:

- do as much as possible with a principled deterministic syntax frontend
- preserve source payload shape honestly
- perform live-state-dependent recovery in an explicit typed elaboration phase
- keep replay execution constrained, generic, and heavily invariant-checked

This is the target architecture.

This document is normative for phase boundaries.
Current-code ownership and migration debt belong in:

- [LAWVM_ARCHITECTURE_INDEX.md](LAWVM_ARCHITECTURE_INDEX.md)

## 1. Problem Statement

Finland is not difficult only because the language is morphologically rich.
It is difficult because the publication artifacts and amendment drafting style
frequently split legal meaning across three places:

- the johtolause surface syntax
- the amendment body payload shape
- the live target statute state

That means a pure grammar is not enough, but it does **not** follow that the
right answer is ad hoc replay-time string surgery.

The architectural mistake to avoid is:

- losing clause structure early
- flattening payload shape too early
- then trying to recover meaning later with free-text filters inside replay

Examples of that bad shape:

- filtering out words like `sellaisena`, `päätöksellä`, `mainitulla`
- broad whole-section operations that should have become row/item operations
- payload normalization that has to infer clause meaning because the frontend
  already erased it

## 2. Layered Architecture

The intended Finland pipeline has five layers:

1. Surface Syntax Frontend
2. Payload Shape Extraction
3. Typed Elaboration
4. Canonical Operation Compilation
5. Replay Execution + Invariants

Each layer has a distinct contract.

### 2.1 Surface Syntax Frontend

Input:

- raw johtolause text

Output:

- a typed clause AST

This layer should capture:

- amendment action families
  - repeal
  - replace
  - insert
  - renumber / move
- target families
  - section
  - subsection
  - item
  - heading
  - intro
  - named row target
- conjunction structure
  - `X sekä muutetaan Y`
  - `X ja muutetaan Y`
  - lists and ranges
- qualifiers and modifiers
  - `sellaisena kuin ...`
  - `viimeksi muutettuna ...`
  - `päätöksellä ...`
  - `mainitulla ...`
  - language qualifiers
  - heading qualifiers

This layer should be deterministic and grammatical as far as the surface text
allows.

### 2.2 Payload Shape Extraction

Input:

- amendment body XML / source tree

Output:

- typed payload IR that preserves real source structure

This layer should preserve, not erase:

- tables and rows
- omission markers
- subsection wrappers
- content-only blobs
- malformed / suspicious structures
- attachment-only or source-incomplete situations

This layer should not pretend malformed source is clean. Shape loss should be
visible and typed.

### 2.3 Typed Elaboration

Input:

- clause AST
- payload IR
- live target tree

Output:

- elaborated typed amendment intents, ready to compile into canonical ops

This is the only phase where live-state-dependent recovery belongs.

Examples:

- sparse payload alignment
- omission expansion
- row-table reconciliation
- section/item/subsection remapping
- inflectional row-name matching
- implicit target completion from conjunction structure
- broad target -> narrow row/item rewrite when payload and live tree justify it

This phase is allowed to be heuristic, but only under constraints:

- heuristics must be typed
- heuristics must be narrow
- heuristics must be reviewable
- heuristics must emit adjudications / hints / proof evidence
- heuristics must not silently collapse into replay execution

### 2.4 Canonical Operation Compilation

Input:

- elaborated typed amendment intents

Output:

- canonical `AmendmentOp` / `LegalOperation`

By this point, replay should not still be discovering basic clause structure.
The frontend/elaboration boundary should already have resolved:

- what is being targeted
- what action is happening
- whether the target is section / subsection / item / row-like item
- whether there is a justified broad-to-narrow rewrite

### 2.5 Replay Execution + Invariants

Input:

- canonical ops
- authoritative live tree

Output:

- new live tree
- explicit adjudications / invariant failures / lints

Replay execution should be the most boring layer.

It should:

- apply ops deterministically
- preserve tree coherence
- reject impossible transformations
- surface suspicious outcomes

Replay execution should **not** be a fallback parser.

## 3. What Grammar Can and Cannot Do

### 3.1 What Grammar Should Own

Grammar should own as much of the following as possible:

- verb detection
- target family parsing
- conjunction structure
- range/list structure
- citation/modifier capture
- clause-level attachment structure
- single-row and multi-row named-target families
- heading/introduction qualification

This is where the frontend must be more principled than the current system.

### 3.2 What Grammar Cannot Fully Own

Grammar cannot fully determine meaning when:

- the payload is sparse
- the amendment body omits coordinates
- the publication shape loses table structure or wrappers
- the statute relies on live numbering / live rows to recover meaning
- a body fragment could map to multiple live targets without state inspection

So “just build a bigger grammar” is not the right answer.

## 4. Why Typed Elaboration Is Necessary

Finland contains genuine underdetermination. Examples:

- a broad `1 §` target with a table body that only changes one or two rows
- a repeal+replace mixed clause where only one payload body is present
- omission markers whose meaning depends on the current live section
- row names appearing in inflected form while live row anchors are nominative

This is not a failure of grammar. It is a sign that the system needs an
explicit elaboration phase between syntax and replay.

The elaboration phase owns:

- row-anchor matchers
- sparse payload normalization
- content-only table-row materialization
- explicit source-pathology detection
- scoped broad-to-narrow rewriting

That keeps the architecture honest.

## 5. Replay Invariants and Universal Guardrails

Across all jurisdictions, replay should keep accumulating:

- structural invariants
- target-consumption accounting
- impossible-tree checks
- duplicate-tract warnings
- duplicate-sibling warnings
- unexpected broad-clobber warnings

These are not substitutes for a good frontend.
They are the safety net behind it.

So the universal architecture is:

- jurisdiction-specific frontend + elaboration
- shared canonical ops
- shared replay constraints and lints

## 6. Design Principles

### 6.1 Preserve Information

Do not throw away:

- clause modifiers
- conjunction structure
- payload structure
- source malformedness

If something looks irrelevant now, it may be necessary later to justify a
broad-to-narrow rewrite or a proof claim.

### 6.2 Keep Heuristics Typed

The system will need heuristics.
The requirement is not “no heuristics”.
The requirement is:

- no free-floating string hacks when a typed phase can own the behavior

### 6.3 Frontload Structure, Delay Underdetermined Meaning

The frontend should parse everything that is structurally parseable.
The elaboration layer should resolve what is only meaningful against live state.
Replay should execute, not interpret.

### 6.4 Make Every Recovery Auditable

Every recovery step should be capable of surfacing:

- what was inferred
- why it was inferred
- what source/live evidence justified it

That supports both strict mode and proof/evidence tooling.

## 7. Immediate Target Shape

Near-term implementation work should follow this order:

1. Grow typed clause AST coverage for the current Finland failure families.
2. Route existing supplement logic through those typed parsers.
3. Move citation/provenance handling out of row-name blacklists and into typed
   modifier nodes.
4. Expand payload elaboration helpers for:
   - row-table mixed clauses
   - single-row content-only section payloads
   - sparse omission-driven subsection alignment
5. Keep strengthening replay invariants and duplicate-text lints.

## 8. Non-Goals

The target architecture is **not**:

- one giant grammar that solves Finland without live state
- pushing more semantic recovery into generic replay
- hiding malformed source behind “best effort” silent normalization
- replacing explicit typed heuristics with opaque model guesses

## 9. Practical Summary

The optimal principled shape is:

- grammar for structure
- payload extraction for honest source shape
- typed elaboration for underdetermined meaning
- canonical ops for replay input
- strict replay with invariants and warnings

That is the target architecture.

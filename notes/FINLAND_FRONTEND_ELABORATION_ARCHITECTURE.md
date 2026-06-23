> **Status (2026-06-22):** Current. Kind: Normative (phase-boundary target architecture; current-code ownership deferred to LAWVM_ARCHITECTURE_INDEX.md). Five-layer model coherent with AGENTS.md invariants; no code-path citations to drift. No findings.

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

1. Add a Finland evidence/workqueue discipline modeled on the UK frontier:
   non-executable claim templates, `executable=false` review scaffolds, source
   witness hashes, manual-frontier status, phase-owned residual classes, and
   typed workqueue exports for cases that are not safe to replay from the
   deterministic frontend. These are evidence surfaces, not replay shortcuts.
2. Grow typed clause AST coverage for the current Finland failure families.
3. Route existing supplement logic through those typed parsers.
4. Move citation/provenance handling out of row-name blacklists and into typed
   modifier nodes.
5. Expand payload elaboration helpers for:
   - row-table mixed clauses
   - single-row content-only section payloads
   - sparse omission-driven subsection alignment
6. Keep strengthening replay invariants and duplicate-text lints.

The first item should make Finland's non-replay frontier inspectable in the
same style as UK: rows that need human/LLM/editorial interpretation must remain
non-executable until a validator emits canonical operations and provenance.

## 8. Finland Non-Executable Frontier

Finland does have a real non-executable frontier. It is narrower than the UK
frontier because the Finland source shape and current frontend are stronger,
but it should be explicit.

The rule is:

- if the source/payload/live-state evidence does not justify one canonical
  operation, the compiler should emit a typed frontier row, not a best-effort
  replay operation
- frontier rows must carry `executable=false` and `replay_authorized=false`
  until a validator turns a manual or LLM compilation claim into canonical ops
- the row must name the owning phase: source acquisition, payload extraction,
  typed elaboration, canonical compilation, replay, oracle comparison, or
  editorial adjudication

Candidate Finland frontier families:

- attachment-only or source-incomplete amendments where the operative legal
  payload is outside the machine-readable law XML
- empty operative bodies or corrigenda whose correction record is visible but
  not safely lowerable into a text or structural operation
- malformed broad section/container replacement bodies where literal replay
  would risk destructive shape loss
- sparse subsection/item payloads whose live slot assignment is not unique
  under the typed elaboration evidence order
- container payloads that overbundle standalone child sections and cannot be
  pruned with a source-proved membership boundary
- missing base-source spans such as textual `Puuttuu luvut 7-11` sentinels
  before a later amendment targets the absent region
- recodification, relabel, or section-identity drift where the executable
  source chain does not prove the migration path
- duplicate or ambiguous live targets where a target phrase could map to more
  than one live carrier
- temporary, expiry, commencement, or transitional overlays where the operative
  point-in-time state cannot be proved from the current timeline
- editorial/oracle artifacts such as inline repeal stubs, synthetic eId
  renames, stale oracle ranges, or metadata-collapsed dates that affect
  comparison but are not themselves executable amendment law
- old clause grammar or semantic continuation forms that remain outside the
  current deterministic Finland clause target surface

The frontier row should include:

- source witness locator and hash when available
- bounded source snippet or payload preview
- affected statute, affected target surface, and candidate live carriers
- candidate operation families, if known
- safe default, usually "do not mutate"
- forbidden shortcut, for example "do not broad-replace the whole section" or
  "do not choose first unmatched slot"
- required proof before replay authorization
- link to the source-pathology/adjudication family that caused the block

Existing source-pathology records are therefore not only diagnostics. Some are
also candidates for non-executable workqueue export when replay would otherwise
guess.

### 8.1 Finland guidance reference packet

Manual-frontier rows may also carry guidance references. These references are
adjudication aids, not executable law and not overrides for the enacted source
text.

The default Finland packet should use:

- Lainkirjoittajan opas, for statute structure, amendment drafting,
  provisions, attachments, commencement, transitional provisions, and repeal
  technique
- Hallituksen esityksen laatimisohjeet (HELO), when a frontier row depends on
  explanatory-material structure, impact-assessment claims, or source
  justification outside the enacted amendment body
- law-drafting process and consultation guides only when the disputed row
  depends on preparatory-material provenance, hearing status, or branch context
  rather than enacted text
- Finlex source/oracle metadata notes when the problem is an editorial
  publication artifact, inline repeal stub, consolidated eId rewrite, date
  collapse, or corrigendum witness

A guidance reference on a frontier row should state:

- guide title and stable URL
- section or heading when available
- why the guidance is relevant to the source-shape or target-ontology question
- whether it supports a deterministic parser rule, a manual claim checklist, or
  only a non-binding triage hint

Examples:

- an unnumbered paragraph peer can cite Finnish statute-structure guidance as
  support for why the XML shape is not a legitimate law-unit ontology
- an attachment-only amendment can cite attachment/statute-structure guidance
  to explain why source acquisition, not replay, owns the missing payload
- commencement or transitional uncertainty can cite commencement/transitional
  drafting guidance to list the required temporal fields before replay can be
  authorized

## 9. Non-Goals

The target architecture is **not**:

- one giant grammar that solves Finland without live state
- pushing more semantic recovery into generic replay
- hiding malformed source behind “best effort” silent normalization
- replacing explicit typed heuristics with opaque model guesses

## 10. Practical Summary

The optimal principled shape is:

- grammar for structure
- payload extraction for honest source shape
- typed elaboration for underdetermined meaning
- canonical ops for replay input
- strict replay with invariants and warnings

That is the target architecture.

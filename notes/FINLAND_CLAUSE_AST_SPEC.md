# Finland Clause AST Spec

Status: living spec, intentionally partial.

Purpose:

- define the intended typed AST for Finland johtolause clause structure
- keep frontend meaning explicit before payload elaboration begins

Related docs:

- [FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md](FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)

## 1. Scope

This spec is about clause structure, not full legal meaning.

It should capture:

- verbs
- targets
- conjunction structure
- qualifiers and modifiers
- target-family distinctions

It should not yet decide:

- sparse payload slot mapping
- omission semantics
- row-table alignment against live state

Those belong to elaboration.

## 2. Core AST Shape

Minimum target shape:

- `ClauseGroup`
  - one or more coordinated amendment clauses
- `Clause`
  - `action`
  - `targets`
  - `modifiers`
  - optional `payload_scope_hints`

The AST should also preserve:

- residual spans the parser could not classify cleanly
- dependent continuation clauses such as `jolloin`
- target facets like:
  - heading
  - intro
  - wording/language-qualified target

### 2.1 Actions

Current intended action families:

- `REPEAL`
- `REPLACE`
- `INSERT`
- `RENUMBER`
- `MOVE`

Not every family must be fully implemented yet, but the AST should keep the
semantic slot explicit.

### 2.2 Targets

A target should be a typed object, not just a string fragment.

Current useful target families:

- section
- subsection
- item / kohta
- heading
- intro (`johd`)
- chapter
- named row target
- section range / target list

### 2.3 Modifiers

Modifiers should survive parsing as typed attachments rather than being dropped
by substring filters.

Examples:

- `sellaisena kuin`
- `viimeksi muutettuna`
- `mainitulla`
- `päätöksellä`
- language qualifiers
- heading qualifiers

### 2.4 Residuals

The frontend should preserve unclassified spans explicitly instead of silently
dropping them.

Why:

- this keeps the AST migration safe
- it makes parser undercoverage visible
- it avoids pretending that unsupported syntax was understood

## 3. Coordination

The AST must preserve coordinated forms like:

- `kumotaan X sekä muutetaan Y`
- `kumotaan X ja muutetaan Y`
- mixed target lists under one verb
- coordinated section labels like `14 a ja 14 b §`

This is one of the main reasons the AST must exist.
Flattening these clauses into raw tokens too early is what creates later replay
debt.

The AST should preserve the coordinator itself, not just the child list:

- `ja`
- `sekä`
- comma continuation

Those links may matter later for how target inheritance or dependent clauses
are interpreted.

## 3.1 Dependent clauses

The AST should support dependent or consequence clauses such as:

- `..., jolloin kohdat e-h muuttuvat kohdiksi d-g`

These are not plain top-level coordination.
They should remain structurally distinct so later elaboration does not need to
recover them from flattened text.

## 4. Named Target Lists

Named row/list families should become first-class AST nodes.

Needed shape:

- `NamedTargetList`
  - one or more row/name references
  - conjunction structure preserved
  - modifiers separated from actual names

Why:

- row-name rewriting should not depend on blacklist-like marker filtering
- target names and citation modifiers are different semantic objects

Named-row targets should also preserve:

- surface text
- grammatical/anaphoric hint if present
- owner structural target

## 5. Chapter Scope

The AST must preserve explicit chapter bindings when the clause really states
them.

Examples:

- `7 luvun 14 a ja 14 b §`
- `7 b luvun 3 §`

This scope should be represented structurally, not reconstructed later from
flat strings if at all possible.

## 5.1 Target facets

The AST should distinguish structural path from target facet.

Examples:

- `1 §:n otsikko`
- `1 §:n 3 momentin johdantokappale`
- `1 §:n ruotsinkielinen sanamuoto`

These should not be encoded only as ad hoc flat `special` strings in the
frontend spec, even if current implementation adapters still do that.

## 6. Clause AST To Payload IR Boundary

The clause AST should be enough to answer:

- what action family is intended
- what target family is intended
- what scope is explicit
- what modifiers belong to citation/provenance rather than to the target

The clause AST should not need to answer:

- which sparse subsection payload maps to which live moment
- whether omission means preserved tail or ambiguous shape loss

That is the handoff point to payload IR and elaboration.

## 7. Current Validated Families

### 7.1 Qualified heading list continuation

Validated by:

- `1991/827` / `2012/751`

Required AST consequence:

- target-list parsing must survive trailing language qualifiers and not truncate
  the coordinated clause early

### 7.2 Named row mixed clauses

Validated by:

- `1995/1292`
- `2006/148`

Required AST consequence:

- mixed repeal/replace row-target clauses should parse into typed target lists
  plus modifiers, not be reconstructed from blacklist markers inside replay

## 8. Open Areas

The AST still needs more concrete node shapes for:

- multi-verb grouped clauses
- deeper heading/introduction qualification
- temporary-law and commencement-specific clause modifiers
- law-level insertion clusters with ranges

Those should be added incrementally as coverage expands.

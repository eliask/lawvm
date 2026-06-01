# UK Frontend / Elaboration Architecture

Status: target architecture for the UK replay frontend.
Kind: frontend contract.

Purpose:

- make UK phase boundaries explicit;
- prevent UK replay work from accumulating as unowned effect-lowering special
  cases;
- preserve the separation between source-only replay, manual-claim replay,
  oracle comparison, and source-pathology adjudication.

Companion specs:

- [SPEC_INDEX.md](SPEC_INDEX.md)
- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [MANUAL_COMPILATION_CLAIMS.md](MANUAL_COMPILATION_CLAIMS.md)
- [UK_REPLAY_LIVING_SPEC.md](UK_REPLAY_LIVING_SPEC.md)
- [UK_REPLAY_REGIME_CONTRACT.md](UK_REPLAY_REGIME_CONTRACT.md)
- [SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md](SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md)
- [CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md)

## 1. Problem Statement

UK replay is difficult because legal amendment meaning is distributed across
several source surfaces:

- official effect metadata;
- affecting-act or affecting-SI XML;
- source-parent context around the extracted provision;
- table, schedule, definition, heading, crossheading, and note structures;
- commencement and extent metadata;
- current/oracle publication shape;
- live target statute state.

The architectural mistake to avoid is treating any one of these surfaces as
the whole instruction.

Examples of bad shape:

- effect metadata names a broad target and replay mutates the whole branch even
  though the affecting source only authorizes a table-row or word-level change;
- source payload is flat `BlockAmendment` text but replay treats it as a whole
  schedule replacement;
- oracle overlap improves because replay over-deletes a branch;
- manual or LLM-assisted interpretation is folded into parser fallback instead
  of entering through a validated claim ledger;
- replay recovers target meaning after canonical operations should already have
  been typed.

## 2. Layered UK Architecture

The UK frontend uses the same phase discipline as the cross-jurisdiction
architecture, but its source regime starts from official effect metadata plus
affecting-source XML rather than Finnish-style johtolause.

The intended UK pipeline has five layers:

1. Effect / Metadata Frontend
2. Affecting-Source Payload Extraction
3. Typed Elaboration
4. Canonical Operation Compilation
5. Replay Execution + Invariants

Each layer has a distinct contract.

### 2.1 Effect / Metadata Frontend

Input:

- official effect feed row;
- affected provision metadata;
- affecting provision metadata;
- effect type;
- extent, territorial, prospective, applied, and date metadata.

Output:

- typed effect metadata facts;
- parsed candidate affected targets;
- parsed candidate affecting-source locators;
- explicit uncertainty, staleness, or conflict records.

This layer owns:

- effect-type normalization;
- affected-target metadata parsing;
- affecting-source locator parsing;
- commencement/prospective metadata capture;
- extent/application metadata capture;
- detection of stale, broad, missing, or internally conflicting metadata.

This layer must not decide that broad metadata authorizes broad mutation where
the affecting source is narrower.

### 2.2 Affecting-Source Payload Extraction

Input:

- typed affecting-source locator;
- archived affecting-act or affecting-SI XML;
- bounded source-parent context.

Output:

- source witness;
- typed payload shape;
- source-parent instruction context;
- source-pathology or extraction-frontier records.

This layer preserves, not erases:

- repeal tables;
- schedule tables and list entries;
- definition entries and definition children;
- headings, crossheadings, sidenotes, and notes;
- `BlockAmendment` / `InlineAmendment` carrier shape;
- parent instructions that supply action, anchor, or text-pair context;
- shell/dot-leader/current-vs-enacted source differences;
- missing, reused, overbroad, or insufficient source fragments.

This layer should never smuggle source carrier text into legal payload just to
make replay executable.

### 2.3 Typed Elaboration

Input:

- effect metadata facts;
- affecting-source payload shape;
- source-parent context;
- live target tree;
- current replay/oracle diagnostics where relevant.

Output:

- elaborated typed amendment intents;
- target-resolution certificates;
- mutation-boundary evidence;
- blocking frontier records where evidence is insufficient.

This is the only phase where UK-specific reconciliation belongs.

Examples:

- metadata says `repealed in part`, but the matched repeal-table row authorizes
  only a word-range omission;
- metadata names a broad schedule, but source-owned descendants identify a
  bounded child replacement;
- source substitutes a labelled series and requires `replace + trailing repeal`;
- pseudo-definition metadata needs a definition-entry or child-boundary proof;
- an `appropriate place` insertion needs a non-executable placement claim;
- prospective metadata is stale and needs affecting-provision
  `RestrictStartDate` evidence for PIT replay;
- oracle retains a repealed branch and the correct result is a compare/oracle
  pathology rather than replay mutation.

This phase is allowed to be heuristic only when:

- the heuristic is typed;
- the source witness is explicit;
- the target boundary is declared;
- the mutation boundary is constrained;
- the result emits evidence or an adjudication;
- strict mode can block rather than guess.

### 2.4 Canonical Operation Compilation

Input:

- elaborated typed amendment intents.

Output:

- canonical `LegalOperation` objects;
- non-executable claim templates where proof is insufficient;
- lowering rejection records where no canonical op may be emitted.

By this point, the compiler must have resolved:

- source surface used as authority;
- action family;
- target family;
- payload shape;
- target-resolution certificate where recovery was needed;
- mutation boundary;
- temporal/PIT applicability if the workload is dated.

If those are not resolved, the row belongs in a deterministic-frontier,
manual-frontier, source-pathology, or compare/oracle bucket rather than replay.

### 2.5 Replay Execution + Invariants

Input:

- canonical operations;
- authoritative live tree;
- replay regime and PIT settings.

Output:

- replayed tree;
- invariant failures;
- replay adjudications;
- mutation events;
- compare/oracle classification.

Replay should be boring.

Replay may:

- apply typed ops deterministically;
- enforce target identity and mutation boundaries;
- reject impossible transformations;
- preserve safe over-retention when evidence is insufficient;
- emit replay-visible diagnostics.

Replay must not:

- discover amendment meaning from broad textual fallback;
- widen a child operation into a parent mutation;
- search across strict target roots after a miss;
- use oracle overlap as authority;
- execute manual/LLM claims that have not passed a validator.

## 3. Regime Separation

UK has several related but distinct regimes.

### 3.1 Source-Only Replay

Source-only replay is the default correctness claim.

It may compile executable operations only from:

- official effect metadata;
- archived public source XML;
- deterministic frontend and elaboration rules;
- explicit temporal and extent evidence.

Its score answers:

> What legal state can LawVM prove from public machine-readable source surfaces?

### 3.2 Manual-Claim Replay

Manual or LLM-assisted work may propose a semantic compile claim, but the claim
must enter through an explicit ledger and validator.

Before execution, a claim must prove:

- source witness;
- target witness;
- action family;
- payload identity;
- placement or text-boundary rule;
- mutation boundary;
- temporal and extent applicability where relevant.

Unvalidated claim templates are non-executable review scaffolds.

### 3.3 Compare / Oracle Classification

Oracle shape is a comparison surface, not legal authority.

Oracle overlap may identify:

- retained repeal branches;
- projection-only descendants;
- wrapper differences;
- stale or missing current-state branches;
- presentation or EID-shape artifacts.

Improving oracle overlap is not a sufficient reason to replay a mutation.

### 3.4 Source Pathology

If source evidence is absent, malformed, shell-only, reused as payload, or
insufficient to prove placement, the row belongs in a typed source-pathology or
frontier class.

Typed non-execution is a correct product.

## 4. Phase Ownership For Common UK Failures

Use this table when assigning UK bugs.

| Failure shape | Owning phase |
| --- | --- |
| Effect type is stale, broad, or misleading | Effect / Metadata Frontend |
| Affected target string parses to the wrong legal address | Effect / Metadata Frontend |
| Affecting source locator resolves to a shell or wrong source row | Affecting-Source Payload Extraction |
| Parent instruction supplies the action but the payload row is anonymous | Affecting-Source Payload Extraction + Typed Elaboration |
| Repeal table row authorizes narrower omission than feed type suggests | Typed Elaboration |
| Broad schedule target has flat payload and no descendant coverage | Typed Elaboration |
| Definition entry placement lacks anchor proof | Typed Elaboration / Manual Claim |
| `appropriate place` lacks ordering proof | Manual Claim |
| Canonical op mutates the wrong sibling or parent | Canonical Operation Compilation or Replay, depending on op correctness |
| Correct op targets a missing strict root | Replay / Source Pathology |
| Oracle retains a source-repealed branch | Compare / Oracle Classification |
| Benchmark score drops after removing over-replay | Measurement, not regression |

## 5. Required Evidence Objects

UK frontend work should converge on these reusable evidence objects:

- source witness;
- affecting-source witness;
- source-parent instruction witness;
- payload-shape record;
- target-resolution certificate;
- mutation-boundary proof;
- temporal/PIT applicability proof;
- extent/application proof;
- source-pathology class;
- compare/oracle pathology class;
- manual-claim template with `executable=false`;
- validated claim ledger entry before manual replay.

## 6. Immediate Work Items

The first UK frontend priority is not another replay fix. It is to make existing
UK behavior phase-owned and auditable.

Near-term work:

1. Annotate the major UK lowering/replay diagnostic families with an owning
   phase from this spec.
2. Add a phase-owner field to UK frontend/lowering rejection records where it is
   not already implicit.
3. Ensure `uk-effects`, `uk-candidates`, `uk-manual-frontier`, and broad
   baseline summaries can group residuals by phase owner.
4. Keep manual claim templates non-executable until a validator emits canonical
   operations and provenance.
5. Prefer adding source-pathology or manual-frontier classes over speculative
   replay mutation when phase evidence is incomplete.

## 7. Non-Goals

Do not copy Finland's johtolause grammar into UK.

Do not promote UK drafting habits into core without a second consumer.

Do not treat source-only and manual-claim replay as one score.

Do not use oracle overlap to justify replay mutation.

Do not solve missing phase ownership by adding broad executor fallback.

## 8. Practical Summary

The UK target architecture is:

- effect metadata frontend for official feed facts;
- affecting-source extraction for public XML witness and payload shape;
- typed elaboration for source/metadata/live-state reconciliation;
- canonical ops only when target, action, payload, temporal applicability, and
  mutation boundary are proved;
- replay as deterministic execution with invariants;
- non-executable claim templates and typed frontier classes for the rest.

This keeps UK hard cases source-faithful without letting the implementation
become a pile of unowned special cases.

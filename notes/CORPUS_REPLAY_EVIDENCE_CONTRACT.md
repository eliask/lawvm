# Corpus Replay Evidence Contract

Status: draft architecture note.
Kind: normative cross-frontend contract.

Purpose:

- define the minimum evidence surfaces for corpus replay jobs across LawVM
  frontends;
- keep acquisition, inventory, compilation, replay, audit, and adjudication
  separable;
- ensure unsupported, skipped, rejected, and uncertain source lanes remain
  visible;
- describe how the contract relates to Estonia, Open Law, and Finland without
  adding implementation requirements to this note.

Scope:

- local archive and local clone inputs;
- inventory manifests;
- operation and effect rows;
- replay and audit rows;
- findings JSONL;
- unsupported, skipped, and rejected rows;
- strict and quirks mode semantics.

Out of scope:

- New Zealand-specific corpus work;
- network acquisition inside replay;
- command-line design;
- code changes;
- jurisdiction-local parser rules.

## 1. Core Claim

A corpus replay run is not only a benchmark. It is an evidence-producing
compiler pass over a declared input set.

For each source artifact or transition that LawVM processes, the corpus output
must answer:

- which local source bytes or git objects were read;
- which stable byte identity was read, normally a content hash such as
  SHA-256 for downloaded artifacts;
- which source lane or witness role each artifact had;
- which source units were inventoried;
- which operations or effects were parsed;
- which operations or effects were accepted, rejected, skipped, or unsupported;
- which typed operations or effects were replayed;
- which replay mutations were applied;
- which audit comparison was performed;
- which findings, source pathologies, adjudications, or unresolved states were
  emitted.

The output must not collapse these into a single success/failure score.

Review tools that consume evidence bundles must preserve their own
input/materialization lane separately from the legal evidence lane. For example,
a review row may have been produced from an artifact bundle, a live cache hit, a
live cache miss, or an uncached live build. That fact is operational provenance:
it explains the review surface and cache behavior, but it does not change source
authority, proof claims, replay adjudications, or legal materialization.

## 2. Input Boundary

Corpus replay must be archive-first and clone-first.

Allowed corpus inputs:

- local archive files;
- local extracted archive directories;
- local git clones;
- local fixture directories;
- local manifests that point to the above.

Network reads are acquisition behavior, not replay behavior. If a frontend needs
to fetch public sources, that step must produce a local archive, clone, or
manifest first. Replay and audit consume only local inputs.

The manifest must preserve enough identity to make later evidence meaningful:

- local path;
- source family;
- source role;
- source identifier used by the jurisdiction;
- content hash or git object id where available;
- archive member path or git ref where relevant;
- acquisition timestamp if known;
- parser/frontend version or contract version if available;
- whether the source is primary, oracle, editorial, witness, fixture, or
  auxiliary.

The manifest records what LawVM read. It does not itself prove legal authority.
Authority and witness roles remain frontend-specific classifications.

## 3. Inventory Manifest

Every corpus run should emit an inventory manifest before replay claims are
made.

The manifest is the durable root of the evidence graph. It should be possible
to inspect the manifest and know which source units were eligible for parsing
even if no operation was ultimately executable.

Minimum manifest surfaces:

- corpus id;
- jurisdiction or frontend id;
- run id;
- input roots;
- source artifacts;
- discovered statute ids, publication ids, amendment ids, transition ids, or
  frontend-local units;
- artifact-to-unit links;
- artifact roles;
- omitted artifact records with reasons;
- frontend assumptions used to construct transitions or source groupings.

Omitted artifacts are not invisible. A file or source unit skipped during
inventory must produce an omitted or skipped inventory record with a reason.

## 4. Operation And Effect Rows

Corpus output should distinguish parsed source effects from executable replay
operations.

An operation/effect row should carry:

- stable row id;
- source artifact id;
- source unit id;
- source location or path where available;
- source text or source XML locator where available;
- frontend-local effect family;
- canonical operation family if lowered;
- original target expression;
- resolved target if available;
- target scope confidence;
- payload summary;
- temporal fields;
- applicability fields;
- accepted, rejected, unsupported, skipped, or failed status;
- blocking status;
- strict-mode disposition;
- quirks-mode disposition;
- finding ids or observation ids linked to the row.

Rows must preserve canonical typed intent. Range expansion, elaboration, and
target recovery may add derived rows or links, but they must not erase the
original source effect.

Unsupported rows are first-class output. They should include the unsupported
family, the source locator, and whether the row blocked replay under the active
mode.

Rejected rows are also first-class output. Any constraint, language-variant
filter, source-pathology filter, applicability filter, body-coverage filter, or
target-safety filter must preserve the rejected row with a reason.

## 5. Replay Rows

A replay row records what LawVM attempted to execute against a live tree.

Minimum replay row fields:

- replay row id;
- run id;
- source operation/effect row ids;
- before state id;
- after state id if replay produced one;
- operation family;
- target path;
- target resolution method;
- target scope confidence;
- changed paths;
- declared target region;
- declared migration paths;
- declared recovery paths;
- failed operation record if execution did not apply;
- findings or observations emitted during replay.

The mutation boundary invariant applies to corpus output:

```text
changed_paths <= target_region(op)
              + declared_migration_paths(op)
              + declared_recovery_paths(op)
              + declared_editorial_projection_paths(op)
```

If this cannot be shown, the replay row must emit a violation or unresolved
finding. A corpus run must not silently convert an out-of-bound mutation into a
successful replay.

## 6. Audit Rows

An audit row compares a replay result to a witness surface. The witness may be a
consolidated statute, publication snapshot, expected fixture, oracle text, or
other frontend-defined surface.

Minimum audit row fields:

- audit row id;
- replay row ids or transition id;
- witness artifact id;
- witness role;
- comparison projection used;
- matched paths;
- differing paths;
- unexplained paths;
- projected presentation differences;
- source-backed differences;
- oracle-only or witness-only differences;
- adjudication or finding ids;
- final audit disposition.

Audit rows classify disagreement. They do not rewrite replay. If a frontend
uses a projection, such as typography normalization or metadata exclusion, the
projection must be named and linked from the audit row.

## 7. Findings JSONL

Findings JSONL is the shared low-friction evidence stream for corpus replay.

Each finding should be a standalone JSON object with:

- stable finding id;
- run id;
- frontend id;
- severity;
- family;
- rule id;
- phase;
- source artifact id;
- source unit id where available;
- related operation/effect row ids;
- related replay or audit row ids;
- message;
- blocking status;
- strict-mode disposition;
- quirks-mode disposition;
- evidence payload.

Findings should use stable rule ids. Human-readable messages may change; rule
ids should not be treated as display text.

The findings stream should include:

- source pathology;
- compile observations;
- target ambiguity;
- unsupported operation families;
- skipped source lanes;
- rejected operations;
- replay failures;
- mutation boundary violations;
- audit disagreements;
- adjudications;
- unresolved states.

## 8. Unsupported, Skipped, And Rejected Rows

The corpus contract forbids accepted-only reporting.

Unsupported means LawVM recognized a source effect family or source lane but
does not currently execute it.

Skipped means LawVM did not process a source unit for a declared reason before
full operation/effect parsing or replay.

Rejected means LawVM parsed enough to know the candidate operation/effect but a
constraint blocked it.

Each category must be visible:

- in the operation/effect surface when operation-shaped;
- in the inventory surface when source-unit-shaped;
- in findings JSONL with a stable rule id;
- in run summary counts.

Strict mode may make these rows blocking. Quirks mode may continue past them.
Neither mode may hide them.

Source selector helpers are part of this rule. A helper that chooses "latest
archived XML", "best manifestation", "current consolidation", or equivalent
must not expose only the winning artifact when newer or otherwise plausible
source candidates were skipped. It may preserve a legacy convenience API, but
the phase-owned API must return diagnostics for missing, malformed, unsupported,
or unavailable candidates with stable rule ids and strict/quirks dispositions.

## 9. Strict And Quirks Semantics

Strict mode means LawVM refuses unproven recovery or unsafe replay.

Strict mode should block or fail rows for:

- fallback target recovery;
- target ambiguity;
- unsupported operation families that affect legal state;
- unowned payload pruning;
- source-lane ambiguity;
- action-family mutation;
- missing body support for a claimed operation;
- unexplained out-of-target mutation;
- ambiguous temporal precedence.

Quirks mode may proceed through known historical or source-shape problems only
when the recovery is named and recorded.

Quirks mode should:

- retain the original row;
- record the recovery or skip rule;
- link findings to affected operation/effect, replay, or audit rows;
- make the same run auditable after the fact.

The mode changes disposition, not evidence visibility.

## 10. Row Relationships

The intended evidence graph is:

```text
manifest artifact
  -> source unit
  -> parsed effect row
  -> accepted/rejected/unsupported/skipped disposition
  -> lowered operation row
  -> replay row
  -> audit row
  -> finding/adjudication rows
```

Not every frontend will populate every edge. Missing edges must mean "not
applicable" or "not implemented" only when that is explicit in row status or
findings.

Do not infer success from absence of evidence.

## 11. Relation To Estonia

Estonia uses replay partly as consistency verification against authoritative
consolidated law.

For Estonia, this contract means:

- source-backed operations and oracle-only shifts stay separated;
- replay rows should identify which source act or source operation produced a
  mutation;
- audit rows should classify consolidated-law disagreements instead of forcing
  parser or replay changes for unsourced oracle drift;
- rejected or unsupported applicability, morphology, target, or clause families
  remain visible as rows and findings;
- frontier hygiene can be built from row dispositions rather than ad hoc
  benchmark filtering.

The contract does not define Estonia parser rules. It defines the evidence
surfaces those rules must leave behind.

## 12. Relation To Open Law

Open Law is a cooperative structured-source regime. Its useful first audit is:

```text
given prior XML + declared codify operations, does the publication snapshot
follow?
```

For Open Law, this contract means:

- local git clones are acquisition inputs;
- publication branches, source commits, editorial-action files, and transition
  assumptions belong in the inventory manifest;
- each `codify:*` action becomes an operation/effect row;
- unsupported actions such as unknown or unimplemented lifecycle families stay
  in the output;
- replay rows record declared target regions and changed paths;
- audit rows compare replay results to codified publication snapshots;
- named projections, such as metadata or typography projections, are audit
  classifications rather than replay rewrites.

The contract does not put Open Law locator syntax or Maryland-specific
vocabulary into core.

## 13. Relation To Finland

Finland is replay-first from amendment acts against non-authoritative editorial
consolidation.

For Finland, this contract means:

- local `.farchive` inputs and extracted source artifacts should be inventoried
  before replay;
- amendment clauses, payloads, elaboration decisions, and canonical operations
  should be connected by stable row ids where possible;
- sparse payload normalization, target recovery, source pathology, and
  elaboration findings should be emitted into the same findings stream;
- Finlex oracle comparison belongs in audit rows, not as a replay rewrite;
- strict mode should block unproven recovery while quirks mode records named
  historical tolerances;
- corpus regressions can assert row families and findings, not only final text
  equality.

The contract does not replace Finland-specific elaboration specs. It defines
the corpus evidence shape those specs should feed.

## 14. Summary Outputs

A corpus run may provide aggregate summaries, but summaries are derived
surfaces.

Useful summary counts:

- artifacts inventoried;
- source units discovered;
- source units omitted or skipped;
- effects parsed;
- operations lowered;
- operations accepted;
- operations rejected;
- operations unsupported;
- replay attempts;
- replay successes;
- replay failures;
- audit matches;
- audit mismatches;
- unresolved rows;
- blocking findings;
- nonblocking findings by family.

Summary output must not be the only place where a skipped, unsupported, or
rejected unit appears.

## 15. Non-Goals

This note does not require all frontends to share one physical file format
immediately.

This note does not define a universal legal authority model.

This note does not make consolidated text automatic truth.

This note does not authorize target broadening, action-family conversion,
payload smuggling, sibling deletion, or parent-granularity escalation.

This note does not add code.

## 16. Implementation Guidance

When implementing this contract later, prefer narrow adapters:

- frontend-local inventory emitters;
- frontend-local operation/effect row exporters;
- shared row schemas only where fields are genuinely cross-frontend;
- stable finding families before aggregate metrics;
- tests that assert unsupported and rejected rows are preserved.

Do not begin by forcing every frontend into a single replay pipeline. Begin by
making each frontend's corpus run tell the truth about what it read, what it
understood, what it executed, what it skipped, and what remains unresolved.

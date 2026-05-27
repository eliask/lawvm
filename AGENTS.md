# LawVM Agent Guide

LawVM treats legislation as an executable state transition system.

Amendment acts are programs written in legal language. They replace, repeal,
insert, renumber, move, delay commencement, restrict scope, and otherwise mutate
a statute tree. LawVM compiles those instructions into typed operations and
replays them over legal text structure.

The output is an auditable account of how legal text-state came to be, which
source facts support it, which repairs were made, and where disagreement or
uncertainty remains.

This file is for agents working in the repository. Read it as an operating
contract, not background prose.

---

## 0. The Prime Directive

**Do not silently delete, mutate, reroute, widen, reorder, or invent legal
state.**

If a repair changes legal structure or text, it must be owned:

1. give the repair a stable rule or finding name;
2. emit a typed observation, finding, source-pathology record, mutation event,
   or failed operation;
3. make strict mode able to reject it when appropriate;
4. add a regression test;
5. explain the source witness or legal reason that makes the repair defensible.

A heuristic is allowed. An invisible heuristic is not.

If the system cannot prove the requested mutation is valid, preserve the
uncertainty. Emit a failure or unresolved finding. Do not “make the tree look
right” by guessing.

---

## 1. Agent Non-Negotiables

### 1.0 If existing code doesn't follow these rules, it must be replaced/fixed

There may be legacy code from learning how lawvm should work.

Anything that violates rules in this guide is not permission to keep doing it.
All such code must be fixed or replaced. When encountering such code, always report it and deliberate what takes highest precedence each time.

### 1.1 No silent target hijacking

If source says:

- chapter 2 / section 5,
- subsection 3,
- item 4,
- a heading facet,
- a chapter container,

then the operation may not be silently applied to some other chapter,
subsection, item, facet, or container because that happens to be the only live
candidate.

Allowed:

- explicit source target resolves exactly;
- inferred target resolves with a named resolver and observation;
- ambiguity becomes a finding or failed operation.

Forbidden:

- “target not found in chapter 2, but section 5 exists in chapter 8, so apply
  there”;
- “item 3 not found in subsection 2, but item 3 exists in subsection 4, so use
  that”;
- “subsection intro target missing, so replace the section intro instead.”

### 1.2 No action-family mutation without ownership

Do not convert legislative verbs invisibly:

- `REPLACE` must not become `INSERT`;
- `INSERT` must not become `REPLACE`;
- `REPEAL item` must not become `REPEAL subsection`;
- range expansion must not drop canonical typed intent.

If recovery really requires changing the executable action, emit a named
finding and keep the original operation traceable.

### 1.3 No granularity escalation

A lower-granularity operation may not overwrite its host.

Examples of forbidden silent escalation:

- item replace overwrites whole subsection;
- item repeal deletes whole subsection;
- subsection replace overwrites section heading;
- child operation mutates parent metadata;
- heading or intro operation falls back to whole-node replacement.

If the source payload is flat or malformed, normalize the payload first with a
named rule, or fail the operation.

### 1.4 No sibling deletion by coincidence

Never delete, merge, or relabel adjacent legal units based only on:

- text equality;
- punctuation;
- string overlap;
- “looks like a carried tail”;
- “same label appears twice”;
- “probably a publisher artifact.”

If sibling deletion or absorption is correct, it must be a named source
normalization or elaboration rule with before/after evidence.

### 1.5 No payload smuggling

A claim on one child does not automatically authorize an entire parent
container.

If an amendment targets section 5, and the source XML wraps it in a chapter
payload, do not admit unrelated sections in that chapter unless they are
claimed, covered by a valid broad target, or explicitly classified as carried
context.

Payload ownership is decided in extraction/elaboration, not late in apply.

### 1.6 No unstated migration

Renumbering, moving, reparenting, or placing existing provisions under a new
container changes identity over time. It must emit migration/lineage evidence.

If code moves existing chapters into a new part, moves a section to a chapter,
or resolves a same-label rebirth, it must leave a lineage trail. Finland may
emit migration events; core should own their PIT/materialization semantics.

### 1.7 No legal conflict resolved by Python accident

Do not resolve competing versions by list order, parser order, dictionary
iteration, or “last one wins” unless the rule is explicitly documented,
tested, and legally/pipeline justified.

Same effective date + same target + incompatible payload is an ambiguity until
a precedence rule proves otherwise.

### 1.8 No unsupported source lane disappears

If a parsed operation is filtered out, rejected, skipped, or downgraded, it must
be visible.

Constraint filters must not return only “accepted operations.” They must also
return rejected operations with reason, source, and blocking/strictness status.

### 1.9 avoid getattr and stringly-typed operations etc without a good reason

### 1.10 avoid try-except too particularly in non-test code

### 1.11 Hot-path performance discipline

Do not make broad performance rewrites without a profiler witness or a bounded
hot-path reason.

Regex policy:

- compile static regexes when they are reused, complex, or run in corpus-scale
  loops;
- do not blindly replace every `re.search(...)` with `re.compile(...)`; Python
  already caches recent module-level regex calls;
- avoid constructing dynamic regex strings inside loops over provisions,
  effects, source XML, or tree descendants;
- if a target-specific dynamic regex is unavoidable, build it once per target
  or function call and reuse the compiled pattern inside the inner loop;
- prefer cheap string guards before regex scans over large legal text;
- replace regex with direct string operations only when the equivalence is
  obvious, tested, and not semantic guesswork.

Performance changes must preserve findings, rejected operations, diagnostics,
and strict-mode behavior. Do not optimize away evidence to improve benchmark
scores or wall time.

---

## 2. What LawVM Optimizes For

LawVM optimizes for:

- auditability over magic;
- typed structure over string patches;
- phase-local diagnosis over late replay repair;
- evidence over confident-looking output;
- bounded investigation over unbounded architecture expansion;
- explicit unresolved states over guessed success.

The correct outcome of a hard case is often not “make replay match the oracle.”
It is “classify why replay, source, and oracle differ.”

---

## 3. Source Regimes and Truth Surfaces

Do not treat consolidated text as automatic truth.

Different jurisdictions expose different truth surfaces:

- amendment acts;
- original promulgation artifacts;
- current editorial consolidations;
- authoritative consolidated law;
- effect feeds;
- corrigenda;
- PDFs;
- HTML views;
- machine-readable XML;
- cached archive snapshots.

The legal role of each surface differs by jurisdiction.

Examples:

- Finland is replay-first from amendment acts against a non-authoritative
  editorial consolidation.
- Estonia uses replay partly as consistency verification against authoritative
  consolidated law.
- The UK is effect-feed and version-graph heavy.
- Norway and Sweden have their own source authority and acquisition problems.

When a replay differs from an oracle, do not assume either side is wrong. First
classify the disagreement.

---

## 4. Repo Map

`src/lawvm/core/` holds the shared kernel:

- IR and legal addresses;
- tree operations;
- timeline and PIT materialization;
- provenance and migration events;
- compile/replay/evidence contracts;
- cross-jurisdiction abstractions.

`src/lawvm/finland/`, `estonia/`, `uk_legislation/`, `norway/`, `sweden/`,
`eu/`, and `us_federal/` hold jurisdiction frontends.

A frontend owns:

- source acquisition;
- source cleaning;
- formula / clause / effect extraction;
- jurisdiction-specific parsing;
- payload normalization;
- elaboration against live state;
- local source-pathology classification;
- oracle/editorial adjudication;
- emission of canonical operations, temporal events, and migration events.

A frontend should not grow its own hidden replay kernel when the issue belongs
in core.

`src/lawvm/tools/` is the CLI and developer/debug surface.

`notes/` is live architecture. Specs, postmortems, work queues, corrigenda, and
case studies there are part of the machine. If you change semantics, update or
read the relevant notes first.

`jurisdiction_starter/` is the contract-first path for new frontends. Do not
copy Finland blindly.

---

## 5. Required Reading Before Non-Trivial Work

For architecture:

- `notes/SPEC_INDEX.md`
- `notes/LAWVM_CONSTITUTION.md`
- `notes/CROSS_JURISDICTION_ARCHITECTURE.md`
- `notes/SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md`

For Finland:

- `notes/FINLAND_ARCHITECTURAL_COHERENCE.md`
- `notes/FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md`
- `notes/FINLAND_CLAUSE_AST_SPEC.md`
- `notes/FINLAND_PAYLOAD_IR_SPEC.md`
- `notes/FINLAND_ELABORATION_RULES.md`
- `notes/CONFORMANCE_CORPUS.md`

For current architectural hazards and recent lessons:

- `notes/LAWVM_ARCHITECTURE_INDEX.md`
- `notes/LAWVM_STACK_MAP.md`
- `notes/REPLAY_INVARIANTS_AND_FAILURE_MODEL.md`
- `notes/SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md`
- any note explicitly named by the user

Historical handoffs, dated case studies, and superseded work queues are not
part of the public v0.1 source tree. Current specs are the authority unless the
user explicitly provides an additional local note.

If a note says a decision is binding, treat it as binding unless the user
explicitly tells you to supersede it.

---

## 6. Phase Ownership

A frontend is a phased compiler. Do not blur these phases.

1. **Acquire** source artifacts.
2. **Clean** source artifacts without changing legal meaning, or emit source
   pathology.
3. **Parse** operative language into clause/effect surface.
4. **Extract** payloads.
5. **Normalize source-local payload shape.**
6. **Elaborate** against live legal state.
7. **Lower** to canonical typed operations/effects.
8. **Replay** typed operations over the live tree.
9. **Compile timelines** and materialize PIT state.
10. **Adjudicate** against oracle/witness surfaces.
11. **Emit evidence** and findings.

Rules:

- Acquisition must not hide which source lane was used.
- Parse must not silently drop operative text.
- Elaboration may recover, but must witness recovery.
- Apply must not invent new semantic meaning.
- Timeline materialization must not silently collapse competing histories.
- Oracle comparison must not rewrite replay; it classifies surfaces.

If a bug is found late, first ask which earlier phase should have exposed it.

---

## 7. Heuristics Policy

Heuristics are unavoidable in historical legal corpora. They are not shameful.
Unowned heuristics are forbidden.

A heuristic that affects legal text, legal structure, target resolution,
timeline selection, or operation filtering must have:

- stable rule ID;
- family tag where relevant;
- source witness or reason;
- before/after summary if it mutates structure or text;
- finding/observation/failed-op emission;
- strict-mode behavior;
- synthetic test;
- real corpus regression if known.

Recommended rule families:

- `transport_cleanup`: mechanical XML/HTML/PDF cleanup with no legal ontology
  implication.
- `ontology_normalization`: source shape violates legal-unit ontology but can
  be repaired.
- `historical_tolerance`: source shape is historically real but outside modern
  drafting expectations.
- `presentation_cleanup`: editorial/oracle display artifact, not law.
- `target_resolution_recovery`: target was under-specified or context-dependent.
- `temporal_recovery`: date/effect/expiry was inferred or corrected.

If you cannot classify the heuristic, do not add it.

---

## 8. Debug and Evidence Contract

When fixing or adding a behavior, ensure the relevant debug/evidence path can
answer:

- Which source artifact was used?
- Which acquisition lane won?
- What operative formula text was extracted?
- What clause/effect surface was parsed?
- What payload surface was extracted?
- Which normalization rules fired?
- Which targets were considered?
- Why was the target selected?
- What canonical operation was emitted?
- What mutation did replay apply?
- What timeline version was selected?
- What migration events were emitted?
- What oracle/witness was compared?
- What finding/adjudication explains the divergence?

A user diagnosing one statute should not need to reverse-engineer the phase
from final text.

---

## 9. Mutation Boundary Invariant

For every operation, the changed paths must be explainable.

Conceptually:

```text
changed_paths ⊆ target_region(op)
             ∪ declared_migration_paths(op)
             ∪ declared_recovery_paths(op)
             ∪ declared_editorial_projection_paths(op)
````

If an operation changes anything outside its target region, that extra mutation
must be declared by:

* a migration event;
* a named recovery/normalization rule;
* an adjudication/editorial projection rule;
* or a failure/violation.

Examples that violate the invariant unless explicitly witnessed:

* item op changes sibling item;
* item op deletes subsection;
* subsection op changes section heading;
* section op moves chapter;
* insert replaces occupied node;
* replace inserts absent node;
* move overwrites native destination;
* timeline materialization hides active descendant;
* duplicate labels cause one node to disappear.

Agents should design fixes around this invariant.

---

## 10. Scope Confidence

Target scope is not binary. Track how it was obtained.

Useful categories:

* `explicit_source`: source text explicitly named the target.
* `explicit_source_with_context`: source named target plus explicit carried
  chapter/part context.
* `inferred_from_group`: target inherited from a grouped amendment formula.
* `inferred_from_payload`: payload shape supplied missing context.
* `inferred_from_live_unique`: live tree had exactly one plausible candidate.
* `fallback`: heuristic recovery.

Rules:

* explicit scope may not be overwritten by live unique fallback;
* fallback scope must emit finding/observation;
* strict mode may reject fallback;
* target ambiguity should remain visible.

Do not “fix” target resolution by broadening search until something matches.

---

## 11. Constraint and Filter Policy

Any function that filters parsed operations must preserve rejected operations.

Bad:

```python
return [op for op in ops if keep(op)]
```

Good:

```python
FilterResult(
    accepted_ops=...,
    rejected_ops=tuple(
        RejectedOp(op=op, constraint_id=..., reason=..., blocking=...)
    ),
    findings=...
)
```

If source produced an operation and LawVM discards it, the ledger must know.

This applies to:

* language-variant filters;
* citation routing;
* source-pathology filters;
* internal-list filters;
* unsafe whole-section replacement constraints;
* unsupported corrigendum verbs;
* omission/body coverage filters.

---

## 12. Core vs Frontend Boundary

Core should own:

* legal address and IR primitives;
* generic tree operations;
* canonical operation/effect carriers;
* timeline and PIT selection semantics;
* migration/lineage semantics;
* structural invariants;
* shared finding/evidence contracts.

Frontend should own:

* local source acquisition;
* local publication quirks;
* local formula language;
* local payload normalization;
* local target-lowering rules;
* local source pathology;
* local oracle/editorial conventions;
* emission of core events and operations.

Do not put Finnish, Estonian, UK, Norwegian, or Swedish drafting idioms into
core unless they are proven as a genuinely shared abstraction. If core must
host an enum or hook used by frontends, document that core does not interpret
frontend-local values.

---

## 13. Timeline, Lineage, and Identity

Provision identity over time is central.

Do not patch identity by address-only rekeying unless it is explicitly marked
as temporary technical debt.

Moves, renumbers, same-label rebirths, native-vs-migrated collisions, and
repeal/reinsert cycles must be represented by lineage/migration semantics.

Core should consume migration events. Frontends should emit them. Frontends
should not compensate forever by late materialization hacks.

If a fix changes address continuity, add:

* synthetic regression;
* real corpus regression if known;
* migration event expectation;
* PIT materialization expectation.

---

## 14. Strict Mode Meaning

Strict mode is not “run without bugs.” It means LawVM refuses unproven
recoveries.

Strict mode should reject or block:

* target guessing;
* fallback scope rebinding;
* action-family mutation;
* unowned payload pruning;
* uncovered-body recovery;
* silent date estimation;
* unsupported applicability dimensions;
* ambiguous temporal precedence;
* source-pathology repairs that are not explicitly allowed.

Quirks mode may proceed through historical mess. But it must record every
quirk.

---

## 15. Tests Required for Meaningful Changes

Every semantic change needs tests at the right level.

Minimum for a new family fix:

1. **Synthetic unit test**
   Small constructed IR/source that isolates the family.

2. **Real corpus regression**
   If the bug was found in a statute, pin that statute/amendment/section.

3. **Finding/observation test**
   Assert the right rule/finding is emitted.

4. **Negative test**
   Show that the rule does not fire on a nearby valid shape.

5. **Strict-mode test** where applicable
   Assert strict mode rejects or records the barrier.

6. **No-leak test** where synthetic internal markers are used
   Opaque labels must not leak into user output, persisted artifacts,
   `LegalAddress`, or `ProvisionTimeline`.

Do not add a corpus-only fix with no synthetic explanation. Do not add a
synthetic-only fix when a real statute motivated it.

---

## 16. Debugging Workflow

For one statute:

```bash
uv run lawvm bisect <ID>
uv run lawvm ops <ID> --source <AMENDMENT_ID>
uv run lawvm dump <ID>
uv run lawvm diff <ID>
uv run lawvm explain <ID>
uv run lawvm oracle-check <ID>
```

For structural violation diagnosis:

```bash
uv run lawvm invariant-bisect <ID> --detector duplicate_label
uv run lawvm invariant-bisect <ID> --detector all_tree --target chapter:4/section:20

uv run lawvm diagnose-phase <ID> --source <AMENDMENT_ID>
uv run lawvm diagnose-phase <ID> --source <AMENDMENT_ID> --target chapter:4/section:20

uv run lawvm snapshot-debug <ID> --source <AMENDMENT_ID> --target section:20
uv run lawvm product-debug <ID> --source <AMENDMENT_ID> --target section:20
```

Useful detectors:

* `duplicate_label`
* `illegal_edge`
* `all_tree`
* `text_duplication`
* `flattened_sublist_family`

Use `--certificate` where available to produce machine-readable diagnostics.

Before patching replay, answer:

1. Is the bad shape already present in source XML?
2. Did acquisition choose the wrong source lane?
3. Did formula extraction drop operative text?
4. Is the change expressed only in body prose?
5. Did target resolution widen or hijack scope?
6. Did sparse elaboration bind the wrong slot?
7. Did apply mutate outside the target?
8. Did replay export lose tombstones or child state?
9. Did PIT materialization collapse migrated/native identities?
10. Is the oracle showing a different editorial or correction layer?

Do not patch the latest visible symptom until the phase-local cause is known.

---

## 17. How to Propose a Fix

A proper fix report should contain:

```text
Problem:
  What legal-state invariant is violated?

Phase:
  acquisition / parse / payload / elaboration / lowering / apply /
  timeline / materialization / oracle / evidence

Family:
  Which reusable interaction family does this belong to?

Source witness:
  Which statute/amendment/source lines or XML shape prove it?

Old behavior:
  What silently happened before?

New behavior:
  What happens now?

Finding/observation:
  What named record is emitted?

Strict mode:
  Proceed, warn, block, or hard fail?

Tests:
  Synthetic:
  Corpus:
  Negative:
```

If you cannot fill this out, the fix is probably too ad hoc.

---

## 18. What Not To Do

Do not:

* hide source defects behind silent cleanup;
* add statute-ID special cases except as tests or documented source-pathology
  fixtures;
* use exact text coincidence as identity;
* use punctuation as the sole structural signal;
* treat missing target as permission to mutate a parent or sibling;
* make body text match an oracle by injecting editorial prose;
* resolve legal ambiguity by parser order;
* drop parsed operations without a rejected-op record;
* rewrite explicit source scope based on a live-tree guess;
* move provisions without migration events;
* put jurisdiction-local strings or regexes into core;
* create synthetic public legal labels;
* overfit a global rule to one broken statute;
* declare “support for a jurisdiction” from current-text parsing alone;
* optimize away findings/adjudication to improve benchmark scores.

---

## 19. AI Agent Task Discipline

Agents must work in bounded tasks.

A good task:

* names the phase;
* names the files likely involved;
* names the corpus regression;
* names the expected finding or invariant;
* has a stop condition.

A bad task:

* “fix Finland bugs”;
* “make benchmark better”;
* “clean up replay”;
* “handle weird XML”;
* “make this match Finlex.”

Agents should not launch architecture expansions opportunistically. If a task
reveals a larger family, stop and write the family diagnosis before coding a
broad fix.

---

## 19.1 Performance Discipline

Performance fixes must preserve the same evidence contract as semantic fixes.
Do not optimize by skipping findings, observations, rejected operations,
strict-mode barriers, source-pathology records, or adjudication detail.

In hot replay, benchmark, extraction, and source-scan paths:

* compile regexes once at module scope or cache them when the pattern is reused;
* avoid repeated full-tree XML or mutable-IR walks inside per-operation loops;
* build explicit indexes for repeated `eId`, sequence, label, table, or source
  lookups;
* stream benchmark rows and reports instead of retaining full-corpus artifacts
  unless a command explicitly needs them;
* make expensive fallback paths visible in timing or diagnostic output when
  they can dominate corpus runs.

Exceptions are allowed for cold code, one-shot scripts, tests, and genuinely
dynamic patterns. If a dynamic pattern appears in a hot loop, document why it
cannot be precompiled or cached.

---

## 20. Agent Final Response Contract

When an agent finishes, it must report:

1. files changed;
2. semantic behavior changed;
3. tests added/changed;
4. findings or observations added;
5. strict-mode behavior;
6. corpus examples verified;
7. known remaining risk;
8. whether the fix is family-level or statute-local.

Never report only “tests pass.”

---

## 21. Current Practical Priority

LawVM has already accumulated enough clever heuristics. The next tier of
quality is explicit ownership of repairs, findings, and phase boundaries.

Highest-value work usually falls into one of these:

1. make destructive repairs visible;
2. prevent explicit target hijacking;
3. preserve canonical typed intent through expansion;
4. move source-lane ambiguity earlier;
5. make lineage/migration core-owned;
6. convert statute-by-statute lore into interaction families.

When in doubt, make the pipeline tell the truth.

---

## 22. Quick Start

LawVM uses `uv`.

```bash
uv sync
uv run lawvm --help
```

Examples:

```bash
# Finland
uv run lawvm replay 2002/738 --as-of 2024-01-01
uv run lawvm explain 2002/738
uv run lawvm bench --help

# Estonia
uv run lawvm -j ee replay <STATUTE_ID> --as-of 2024-01-01
uv run lawvm verify-consistency --jurisdiction ee --base <BASE_ID> --consolidated <ID>

# UK
uv run lawvm uk-replay <STATUTE_ID> --pit-date 2024-01-01
uv run lawvm uk-fetch-affecting <STATUTE_ID>

# Norway
uv run lawvm no-index
uv run lawvm no-verify <BASE_ID> --as-of 2024-01-01

# Sweden
uv run lawvm sweden --help
```

Many workflows require local archived sources under `data/*.farchive`.
Reproducibility should be archive-first whenever possible.

---

## 23. The Real Output

The long-term output of LawVM is a legal execution substrate:

* point-in-time text as materialization;
* provision timelines as executable history;
* amendment lineage as provenance;
* replay-vs-oracle classification as evidence;
* source pathology as a first-class lane;
* cross-statute references, delegations, and breakage as graph queries;
* jurisdiction frontends that make bounded, source-backed claims.

The text is not decoration. The notes are not decoration. The findings are not
decoration.

They are part of the machine.

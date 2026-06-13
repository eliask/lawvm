# jurisdiction_starter

This directory is the contract-first starter kit for a new LawVM jurisdiction frontend.

It exists to stop new frontends from beginning as a pile of parser experiments. Before code grows, the jurisdiction must declare:

- what the trustworthy source families are,
- what the frontend is trying to prove,
- which LawVM phases will exist, be compressed, or remain blocked,
- which adjudications belong to source/pathology vs compare-shape vs replay,
- how success will be evaluated,
- how humans and agents are allowed to work.

This starter is downstream of:

- `notes/LAWVM_CONSTITUTION.md`
- `notes/THEORY_OF_LAWVM.md`
- `notes/CROSS_JURISDICTION_ARCHITECTURE.md`
- `notes/LAWVM_PROOF_SURFACES.md` (the shared object grammar + core modules a
  frontend must reuse — see the next section)
- `notes/SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md`
- `notes/CORPUS_REPLAY_EVIDENCE_CONTRACT.md`
- `notes/JURISDICTION_CLI_TOOLING_CONTRACT.md`

Those documents govern this starter. If this starter conflicts with them, the
current LawVM constitution and cross-jurisdiction contracts win.

The freshest worked exemplar of this whole path is the New Zealand frontend
under `src/lawvm/new_zealand/`. When in doubt about *how a step is actually
done now*, read NZ before improvising. NZ is archetype 4 below
("API/feed-backed corpus").

---

## Reuse the shared core, and prove replay with dry-run first

Two lessons from the New Zealand build are not yet baked into the older files in
this folder. They are the highest-leverage things to get right.

### 1. Reuse the shared core proof-surface objects before writing local ones

`notes/LAWVM_PROOF_SURFACES.md` §2 defines the object grammar every frontend
flows through:

```text
SourceWitness -> Claim/Assertion -> ExecutionAuthorization -> Proof
             -> Materialization -> Agreement -> Residual/FrontierWorkItem
```

These are concrete shared modules under `src/lawvm/core/` (e.g.
`source_witness.py`, `evidence_surface_report.py`, `proof_surfaces.py`,
`execution_authorization.py`, `mutation_boundary_proof.py`,
`agreement_residual.py`, `frontier_work_item.py`, `frontend_phase_surface.py`,
`ir.py`, `temporal.py`, plus `comparison_normalization.py` and jurisdiction-
neutral tools `tools/spec_ledger.py`, `tools/self_consistency.py`,
`tools/_parallel_corpus.py`). NZ imports these directly. A new frontend should
**reuse these objects before inventing local report/agreement/replay shapes**.
`FILE_MAP.md`'s module layout is for the *jurisdiction-local plugin* on top of
this core, not a license to rebuild the core.

### 2. Dry-run before actual replay

A new frontend earns replay one operation family at a time. Each family is first
proven in a **dry-run surface**: apply the candidate operation to an immutable
parsed *before* tree, materialize a candidate *after* tree, and compare it
against the archived before/after oracle with a **mutation-boundary proof** and
**typed refusals**. Actual replay stays *blocked* until the dry-run surface
agrees with the oracle. NZ shipped `repeal` and `text_replace` exactly this way
(`src/lawvm/new_zealand/dry_run*.py`). The oracle is a *witness, not ground
truth*: residuals carry a typed disposition (`lawvm_wrong` vs `oracle_suspect`
vs `missing_source`); never silently repair to match an oracle (Constitution §9).

### 3. Pin a monotone coverage north-star

Measure coverage against a denominator that is a **fact of the source** (count
of ground-truth operation witnesses, e.g. from provision history notes), not a
candidate-derived count that grows as extraction improves. A witness-anchored
denominator makes progress monotone and comparable across cycles. See
`src/lawvm/new_zealand/dry_run_north_star.py` and `tools/spec_ledger.py`
("the deliverable is the discovered spec; every behaviour is a named, falsifiable
rule hypothesis carried as a witness rule id").

---

## How to use this folder

1. Copy this directory to a working name such as `jurisdiction_<code>_starter/`.
2. Replace placeholder values like `<JURISDICTION>` and `<CODE>`.
3. Fill the files in this order:
   - `JURISDICTION_PROFILE.md`
   - `SOURCE_STRATEGY.md`
   - `PHASE_PLAN.md`
   - `ADJUDICATION_PLAN.md`
   - `EVAL_PLAN.md`
   - `ROADMAP.md`
4. Then fill:
   - `FILE_MAP.md`
   - `API_CORPUS_FRONTEND_ADDENDUM.md` if the jurisdiction exposes an API,
     feed, local git corpus, or other corpus-wide source index
   - `AI_AGENT_PROTOCOL.md`
   - `TASK_CARD_TEMPLATE.md`
   - `REVIEW_CHECKLIST.md`
5. Only after those are coherent should code be started.

The starter is considered ready when a reviewer can answer all of the following without guesswork:

- What is the base-source story?
- What is the amendment-source story?
- What is the verification/oracle story?
- What local archive, clone, fixture, or manifest is the replay substrate?
- What inventory manifest is emitted before any replay claim?
- Where are unsupported, skipped, and rejected rows preserved?
- Where is findings JSONL emitted, and which stable rule ids can appear?
- What phases are real, compressed, synthetic, or blocked?
- What are the first executable artifacts?
- What does “replay-capable” mean for this jurisdiction?
- What evidence-pack summary separates claimed rows from non-claimed rows?
- What evidence would prove that a divergence is source-sparse rather than replay-bug?
- If acquisition depends on an API, feed, or source graph, how does the
  frontend resume, respect rate limits, and preserve acquisition-frontier
  state?
- What is the source-complete tight-loop corpus, and which rows are excluded
  into pending/source-sparse/notruth partitions?
- Which CLI tools expose inventory, corpus curation, bench history, per-row
  source dumps, operation/effect inspection, phase diagnosis, bisect/blame,
  frontier ranking, evidence export, and structural review?

---

## What this starter is for

Use this starter when a jurisdiction is not yet implemented or only partially implemented.

It is for turning “we think this jurisdiction is possible” into a reviewable build plan.

It is not for:
- writing production code directly,
- hiding gaps behind optimistic TODOs,
- claiming replay support from current text alone,
- treating network acquisition as replay,
- reporting only accepted operations,
- letting agents improvise architecture from exemplars.

---

## What a completed starter should produce

A completed starter should make it easy to derive:

- a source acquisition plan,
- a first module/file skeleton under `src/lawvm/<code>/`,
- an initial eval corpus,
- task cards that an agent can implement independently,
- review criteria for whether the work followed LawVM philosophy.

If this directory is good, an agent should be able to build one bounded phase with high assurance and low architectural drift.

---

## Corpus evidence floor

Every jurisdiction starter must declare the minimum evidence surfaces from
`notes/CORPUS_REPLAY_EVIDENCE_CONTRACT.md`.

Required starter commitments:

- Replay and audit consume a local source substrate: archive files, extracted
  archive directories, local git clones, fixture directories, or manifests that
  point to them. Live network reads belong to acquisition, not replay.
- Inventory comes first. A run must be able to emit an inventory manifest before
  parser, compiler, replay, or verification claims are made.
- Unsupported, skipped, and rejected source units or operation-shaped rows are
  preserved with status, reason, blocking disposition, and source locator. They
  must not disappear from reports just because they were not accepted.
- Findings JSONL is the shared low-friction evidence stream. Stable `rule_id`
  values matter more than prose messages.
- Evidence-pack summaries distinguish claimed rows from non-claimed rows:
  accepted/replayed/audited claims are counted separately from unsupported,
  skipped, rejected, failed, blocked, unresolved, or non-claim rows.

Example surfaces live under `examples/` and should be copied or narrowed for the
jurisdiction rather than weakened.

## CLI and tight-loop benchmark floor

Every jurisdiction starter must declare the developer/debug tooling needed to
iterate without guessing. Follow `notes/JURISDICTION_CLI_TOOLING_CONTRACT.md`.

Required starter commitments:

- The full corpus is an outer regression guard, not the primary optimization
  target.
- A source-complete tight-loop corpus is defined before serious replay-quality
  work. The usual target size is 100-400 rows, plus a smaller smoke/canary set.
- Source-complete means the local replay substrate has the base/enacted source,
  amendment/effect source or equivalent semantic source, oracle/verifier source,
  and source-status/hash/locator evidence needed for the jurisdiction's claim.
- Rows missing required truth surfaces are preserved in explicit
  `pending`, `source_sparse`, `notruth`, or equivalent partitions. They are not
  silently mixed into the tight-loop replay score.
- The frontend must expose or plan commands for inventory, corpus curation,
  bench history/compare/regression, source dump, operation/effect inspection,
  phase diagnosis, bisect/blame, frontier ranking, evidence export, and compact
  non-interactive structural review.
- Shared flags keep shared meanings. In particular, `--corpus` must select the
  curated corpus input for that jurisdiction rather than being ignored.

---

## Design rule

A new jurisdiction should begin with the smallest honest executable claim.

Examples:

- “We can parse current text into IR, but not replay history.”
- “We can compile official amending acts for section replacements only.”
- “We can replay a post-2015 subset with explicit commencement dates.”
- “We can verify against current official text, but not historical snapshots.”

Those are good claims.

“Probably works for most statutes” is not.

---

## Common frontend archetypes

Most jurisdictions will resemble one of these shapes.

### 1. Structured-amendment source exists
Examples: Norway-like, UK-like.

Good news:
- clause lowering may be partly pre-done by the source.

Obligation:
- still emit synthetic clause/payload/effect artifacts instead of silently skipping waists.

### 2. Official promulgation text exists, but amendment semantics live in prose
Examples: Sweden-like.

Good news:
- official source chain is often honest and auditable.

Obligation:
- clause surface and payload extraction become first-class engineering work.

### 3. Current consolidated text is rich, historical acts are sparse
Good news:
- verification and current IR may be easy.

Obligation:
- do not confuse current surface with replay substrate.
- historical rebuild may require staged source recovery.

### 4. API or feed-backed corpus exists
Examples: New Zealand-like, registry-backed, or local git-corpus frontends.

Good news:
- corpus inventory, current versions, and historical consolidated snapshots may
  be obtainable without scraping presentation HTML.

Obligation:
- live API reads remain acquisition only; replay and audit consume the local
  archive, clone, or manifest produced by acquisition.
- rate limits, beta API gaps, pagination, and incomplete source lanes must be
  recorded as acquisition diagnostics rather than hidden by retry loops.
- dependency closure from current/history notes, effect feeds, or version graph
  records is a source claim, not proof that amendment semantics were compiled.

---

## Completion gates

Do not move to production coding until this starter can answer:

- Which source family seeds the base act?
- Which source family yields amending semantics?
- Which source family verifies replay?
- Which phases are impossible today, and why?
- What is the narrowest first replay subset?
- What source contamination risks exist?
- What eval would detect architectural cheating?

If those answers are not written down here, the frontend is not ready to build.

---

## Suggested next step after this folder is filled

Create the initial repo layout described in `FILE_MAP.md`, then generate one task card from `TASK_CARD_TEMPLATE.md` for the first bounded phase artifact.

## Runtime scaffold for blocked P5 work

`p5_runtime_scaffold.py` provides a narrow runtime helper for jurisdictions
that have inventoried amendment source units but have not implemented P5 clause
surface parsing. It emits blocked clause-surface rows, blocking P5 findings,
and a non-claim summary with zero operation/effect rows and zero replay
attempts.

Use it only as a transparency bridge. It does not parse clauses, lower payloads,
emit canonical effects, or claim replay support.

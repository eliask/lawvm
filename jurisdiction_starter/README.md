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
- `notes/CROSS_JURISDICTION_ARCHITECTURE.md`
- `notes/SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md`

Those documents govern this starter. If this starter conflicts with them, the
current LawVM constitution and cross-jurisdiction contracts win.

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
   - `AI_AGENT_PROTOCOL.md`
   - `TASK_CARD_TEMPLATE.md`
   - `REVIEW_CHECKLIST.md`
5. Only after those are coherent should code be started.

The starter is considered ready when a reviewer can answer all of the following without guesswork:

- What is the base-source story?
- What is the amendment-source story?
- What is the verification/oracle story?
- What phases are real, compressed, synthetic, or blocked?
- What are the first executable artifacts?
- What does “replay-capable” mean for this jurisdiction?
- What evidence would prove that a divergence is source-sparse rather than replay-bug?

---

## What this starter is for

Use this starter when a jurisdiction is not yet implemented or only partially implemented.

It is for turning “we think this jurisdiction is possible” into a reviewable build plan.

It is not for:
- writing production code directly,
- hiding gaps behind optimistic TODOs,
- claiming replay support from current text alone,
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

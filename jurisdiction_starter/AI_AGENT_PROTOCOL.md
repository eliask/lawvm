# <JURISDICTION> AI agent protocol

This file defines how agents may contribute to this frontend.

The aim is not “let the agent do everything”. The aim is bounded independent development with high assurance.

---

## 1. Core rule

Agents may implement bounded tasks against explicit phase contracts.

Agents may not invent doctrine.

Humans retain authority over:
- source strategy,
- phase compression decisions,
- new adjudication families,
- widening core waists,
- changing what counts as replay support.

---

## 2. Conditions under which agent development is allowed

Agents may work independently only if all of the following exist:

- completed `JURISDICTION_PROFILE.md`,
- completed `SOURCE_STRATEGY.md`,
- completed `PHASE_PLAN.md`,
- completed `ADJUDICATION_PLAN.md`,
- completed `EVAL_PLAN.md`,
- declared local substrate and inventory manifest shape,
- declared findings JSONL rule-id policy,
- a concrete task card for the bounded unit of work,
- at least one fixture for the task.

If those do not exist, agent work is exploratory only and must not be treated as production-ready.

---

## 3. Allowed task shapes

Good agent tasks are phase-bounded and artifact-bounded.

Examples:
- archive one source family with locators,
- implement current IR parse for chapters/sections only,
- emit synthetic clause surface from structured amendment metadata,
- compile section-replace effects from official acts,
- classify divergence partitions from verification rows.

Bad agent tasks:
- “build the entire frontend”,
- “make Sweden historical replay work”,
- “port Finland approach to jurisdiction X”.

---

## 4. Required outputs from each agent task

Every agent task must end with:

- code changes,
- updated artifact schema or example if needed,
- inventory, row, finding, or evidence-pack impact explicitly stated,
- at least one fixture,
- a machine-readable report or test result,
- a short note stating what remains unsupported.

Narrative confidence without artifacts does not count.

---

## 5. Multi-agent pattern

The recommended pattern is:

### Planner
Turns a goal into a bounded task card against the phase contracts.

### Implementer
Builds the code and artifacts for exactly that task.

### Verifier
Checks:
- contract compliance,
- fixture behavior,
- eval deltas,
- whether the task widened scope implicitly.

A human reviews doctrine and merge-worthiness.

---

## 6. Stop rules for agents

The agent must stop and escalate when:

- the task requires a new phase compression decision,
- the source strategy appears wrong,
- a new adjudication family seems necessary,
- the task needs unbounded heuristics across multiple phases,
- the agent can only proceed by using current text as historical proof,
- the task would drop unsupported, skipped, or rejected rows from evidence,
- the task would treat live network fetches as replay substrate,
- the task would require changing shared LawVM core types materially.

---

## 7. Merge rules for agent work

Reject the change if any of these are true:

- the code “works” but the phase artifact is not inspectable,
- the agent hid uncertainty behind default fallbacks,
- the task expanded beyond the task card,
- the change created a catch-all adjudication,
- verification got better only because compare normalization widened,
- the artifact or fixture cannot explain why the result is correct.

---

## 8. What makes independent agent development feasible

Independent agent development becomes realistic when the jurisdiction starter has already done the hard cognitive work:

- source families are ranked,
- local substrate and inventory outputs are declared,
- waists are explicit,
- artifacts are named,
- unsupported space is declared,
- rejected and skipped row handling is declared,
- findings JSONL and evidence-pack summary rules are declared,
- evals are ready,
- task units are small.

Without that, the agent is forced to re-derive the architecture from exemplars and will drift.

---

## 9. Recommended human cadence

Humans should review at these checkpoints:

- after source strategy,
- after phase plan,
- after first real artifact per phase,
- after first end-to-end replay,
- after first verification partition report.

That keeps doctrine centralized while implementation can still be delegated.

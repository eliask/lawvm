# <JURISDICTION> eval plan

This file defines how the frontend will be judged.

The purpose is not only to measure success. It is to prevent fake success caused by contamination, overfitting, or using the wrong source as oracle.

---

## 1. Eval ladder

Every new frontend should have at least four layers.

### Layer A. Artifact unit tests
Does one parser/compiler phase produce the right artifact from one small source?

Examples:
- source record fields,
- current IR parse,
- official-act parse,
- clause surface rows,
- payload extraction rows,
- canonical effect compilation.

### Layer B. Golden fixture tests
Does a small fixed source bundle produce the expected artifacts across multiple phases?

### Layer C. End-to-end replay tests
Given a base seed and amending source, does replay reach the expected post-state?

### Layer D. Oracle verification tests
Does replay agree with an independent oracle, and if not, is the divergence classified correctly?

---

## 2. Fixture design

Define the fixture families.

| Fixture family | Why it exists | Minimum count | Notes |
|---|---|---:|---|
| Simple replace |  |  |  |
| Simple insert |  |  |  |
| Repeal |  |  |  |
| Renumber |  |  |  |
| Word-level text change |  |  |  |
| Commencement edge case |  |  |  |
| Heading/appendix/table edge case |  |  |  |
| Known contamination case |  |  |  |

At least one fixture should be chosen specifically because it is expected to be hard.

---

## 3. Anti-cheating rules

The eval suite must guard against these failure modes:

- using current consolidated text as the replay base when measuring historical replay,
- using the same artifact as both replay substrate and oracle,
- silently skipping hard ops and counting the case as success,
- accepting benchmark improvements caused only by compare normalization,
- hiding unsupported cases outside the eval corpus.

State how this jurisdiction’s evals prevent those.

---

## 4. Metrics

Choose a small set of metrics.

Recommended:
- artifact parse pass rate,
- canonical-effect compilation pass rate,
- replay success rate,
- verified end-state match rate,
- divergence partition counts,
- unsupported/blocked rate.

Do not use a single vanity metric.

---

## 5. Verification partitions

The verification layer should report counts for:

- exact / verified match,
- editorial-only mismatch,
- compare-shape-only mismatch,
- source-sparse mismatch,
- replay-defect mismatch,
- blocked/unsupported,
- error.

If the jurisdiction needs extra buckets, add them here.

---

## 6. Promotion gates

### Gate 1: source-legible
- inventory exists
- source record fixtures pass
- acquisition and locators are archived

### Gate 2: current-parse-legible
- current IR fixtures pass
- structural invariants hold
- contamination cases are recognized

### Gate 3: amendment-legible
- official-act / structured-amendment fixtures pass
- clause/payload/effect artifacts exist
- unsupported cases are typed

### Gate 4: replay-legible
- replay fixtures pass
- skips become adjudications
- invariants are checked

### Gate 5: oracle-legible
- verification exists
- partitions are stable
- source-sparse and replay-defect are not conflated

---

## 7. Benchmark selection policy

The benchmark set must include:

- easy cases,
- representative normal cases,
- adversarial cases,
- at least one case expected to fail at the current phase.

A benchmark set that contains only successes is not useful.

---

## 8. Reporting outputs

Expected artifacts:
- `fixture_report.json`
- `compile_report.json`
- `replay_report.json`
- `verify_report.json`
- `partition_report.json`

These should be serializable and diffable so agents can be judged by artifact deltas rather than narrative claims.

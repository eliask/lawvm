# <JURISDICTION> adjudication plan

This file defines where uncertainty lives.

The goal is to stop three different problems from being collapsed into one vague “mismatch” bucket:

1. source pathology,
2. compare-shape / oracle-shape noise,
3. replay defect.

---

## 1. Core rule

A frontend must not accuse replay when the evidence only supports a source or oracle problem.

Likewise, a frontend must not hide replay defects under “source weirdness”.

---

## 2. Required families

Every jurisdiction should classify findings into these families.

### A. Source pathology
Used when the source evidence is missing, ambiguous, malformed, or semantically insufficient.

Examples:
- no extracted amending text,
- reference-only target clue,
- instruction text reused as payload,
- commencement date absent / contingent / unresolved,
- current surface contaminated by future structure.

### B. Compare-shape / oracle-shape
Used when replay and oracle differ mostly because the oracle collapses or wraps structure differently.

Examples:
- collapsed subtree in oracle,
- wrapper-only node absent in oracle,
- retained editorial heading,
- table-layout-only mismatch.

### C. Replay defect
Used only when replay-owned logic has direct evidence of failure.

Examples:
- target not found when it should exist,
- payload missing,
- unsupported action at replay time,
- invariant violation,
- wrong renumber order,
- text-replace miss on found target.

---

## 3. Shared-first policy

List the shared adjudications you expect to reuse from core LawVM.

- shared source pathology kinds:
- shared compare-shape kinds:
- shared replay bug kinds:

List only the genuinely jurisdiction-specific kinds that remain local.

- `<code>_...`
- `<code>_...`

If a local kind exists only because the shared kind does not yet exist, say so explicitly.

---

## 4. Ownership map

| Claim | Phase owner | Allowed adjudication family |
|---|---|---|
| source artifact missing | P1 | source pathology |
| identity ambiguous | P2 | source pathology |
| clause could not be lowered | P5 | source pathology |
| payload could not be extracted | P6 | source pathology |
| canonical effect unsupported | P7 | source pathology or unsupported |
| replay target not found | P8 | replay defect |
| replay invariant broken | P8 | replay defect |
| oracle collapsed wrapper | P9 | compare-shape |
| sparse source history | P9/P10 | source-sparse / source pathology |

---

## 5. Minimum local taxonomy for a new frontend

Fill this in with concrete names.

### Source pathology
- `<kind>` — meaning:
- `<kind>` — meaning:

### Compare-shape
- `<kind>` — meaning:
- `<kind>` — meaning:

### Replay defect
- `<kind>` — meaning:
- `<kind>` — meaning:

---

## 6. Partition rules for verification

When verification diverges, the report should try to partition into:

- consistent
- replay_defect
- compare_shape_only
- source_sparse
- untouched_drift
- blocked / unsupported
- error

Define how this jurisdiction will detect each partition.

---

## 7. Proof threshold for replay bugs

State the threshold.

Template:

> A row is only upgraded to replay defect when replay emitted a replay-owned adjudication or when a deterministic invariant/contract was violated. Residual mismatch alone is not enough.

Keep or refine that rule here.

---

## 8. Review tests

Before merge, reviewers should ask:

- Does this new adjudication belong in source, compare, or replay?
- Could this be shared instead of local?
- Does it preserve proved-vs-unresolved distinction?
- Does it narrow a phenomenon, or create a catch-all bucket?

If the answer is “catch-all bucket”, reject it.

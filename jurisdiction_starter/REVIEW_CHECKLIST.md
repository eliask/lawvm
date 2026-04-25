# <JURISDICTION> review checklist

Use this before merging any meaningful frontend work.

---

## A. Constitution checks

- [ ] The change preserves source honesty.
- [ ] The change does not collapse multiple phase claims into one opaque parser/replayer.
- [ ] The change does not use the oracle as replay substrate.
- [ ] Unsupported phenomena became typed adjudications or blocked capability.
- [ ] Provenance is still recoverable.

---

## B. Phase checks

- [ ] The owning phase is clear.
- [ ] Inputs/outputs are explicit.
- [ ] The new artifact is serializable.
- [ ] The artifact is inspectable without re-running the whole stack.
- [ ] Compression, if any, still emits a synthetic equivalent artifact.

---

## C. Adjudication checks

- [ ] Source pathology is not mislabeled as replay defect.
- [ ] Compare-shape is not mislabeled as replay defect.
- [ ] Replay bug claims have replay-owned evidence.
- [ ] New local kinds are genuinely local.
- [ ] No catch-all bucket was introduced.

---

## D. Eval checks

- [ ] There is at least one fixture for the change.
- [ ] The fixture would fail without the new behavior.
- [ ] Improvement is not due only to compare normalization.
- [ ] Unsupported cases remain visible in reports.
- [ ] End-state verification still uses an independent oracle.

---

## E. Agent-work checks

- [ ] The work followed a task card.
- [ ] The task card scope matches the code.
- [ ] The agent did not silently change doctrine.
- [ ] Reports/artifacts justify correctness.
- [ ] A human reviewed any architectural boundary changes.

---

## F. Readiness checks

Do not claim:
- “replay supported” unless P8 is real and typed,
- “verified replay supported” unless P9 is real,
- “historical replay supported” unless contamination/recovery is addressed.

# <JURISDICTION> review checklist

Use this before merging any meaningful frontend work.

---

## A. Constitution checks

- [ ] The change preserves source honesty.
- [ ] Replay/audit consumes local archive, clone, fixture, or manifest substrate.
- [ ] The change does not collapse multiple phase claims into one opaque parser/replayer.
- [ ] The change does not use the oracle as replay substrate.
- [ ] Unsupported phenomena became typed adjudications or blocked capability.
- [ ] Skipped and rejected rows remain visible with reasons.
- [ ] Provenance is still recoverable.

---

## B. Phase checks

- [ ] The owning phase is clear.
- [ ] Inputs/outputs are explicit.
- [ ] Inventory is emitted before replay or verification claims.
- [ ] API/feed/git acquisition, if present, emits local substrate before replay
      and does not read live sources during replay/audit.
- [ ] Long-running or rate-limited acquisition, if present, has resumable
      frontier state and visible diagnostics.
- [ ] The new artifact is serializable.
- [ ] The artifact is inspectable without re-running the whole stack.
- [ ] Compression, if any, still emits a synthetic equivalent artifact.

---

## C. Adjudication checks

- [ ] Source pathology is not mislabeled as replay defect.
- [ ] Compare-shape is not mislabeled as replay defect.
- [ ] Replay bug claims have replay-owned evidence.
- [ ] Findings JSONL contains stable rule ids for new findings.
- [ ] New local kinds are genuinely local.
- [ ] No catch-all bucket was introduced.

---

## D. Eval checks

- [ ] There is at least one fixture for the change.
- [ ] The fixture would fail without the new behavior.
- [ ] Improvement is not due only to compare normalization.
- [ ] Unsupported cases remain visible in reports.
- [ ] Skipped/rejected cases remain visible in reports.
- [ ] Evidence-pack summary separates claim and non-claim counts.
- [ ] End-state verification still uses an independent oracle.
- [ ] Corpus-acquisition fixtures cover pagination, unavailable artifacts, or
      rate-limit behavior where those are part of the frontend boundary.

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

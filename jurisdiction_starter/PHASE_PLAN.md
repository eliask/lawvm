# <JURISDICTION> phase plan

This file maps the jurisdiction onto LawVM phase contracts P0–P11.

Do not merely say “supported” or “TODO”. State what artifact exists, who owns the claim, and what the first bounded implementation will be.

---

## 1. Phase summary

| Phase | Status | Artifact | Owner | First bounded implementation |
|---|---|---|---|---|
| P0 Capability/inventory |  | `inventory_manifest.json` plus omitted/skipped rows |  |  |
| P1 Acquisition/archive |  |  |  |  |
| P2 Source record |  |  |  |  |
| P3 Current IR parse |  |  |  |  |
| P4 Official-act parse |  |  |  |  |
| P5 Clause surface |  |  |  |  |
| P6 Payload surface |  |  |  |  |
| P7 Canonical effects |  |  |  |  |
| P8 Replay/materialization |  |  |  |  |
| P9 Verification |  |  |  |  |
| P10 Recovery/historical rebuild |  |  |  |  |
| P11 Reporting/work queues |  | `findings.jsonl`, evidence-pack summary, work queues |  |  |

Allowed statuses:
- `real`
- `synthetic`
- `compressed`
- `blocked`
- `later`

---

## 2. Per-phase notes

For each phase, answer:

- Input artifacts:
- Output artifacts:
- Row ids and source links:
- Unsupported/skipped/rejected row behavior:
- What this phase may claim:
- What it may not claim:
- Main failure modes:
- Required adjudications:
- Exit criterion:

### P0
### P1
### P2
### P3
### P4
### P5
### P6
### P7
### P8
### P9
### P10
### P11

---

## 3. Phase compression rules

If any phases are compressed, document the synthetic equivalent artifact.

Example language:

> P5 is synthetic because the source already gives explicit target references.  
> The frontend will still emit `clause_surface.json` whose rows record the source instruction, target family, evidence span, and confidence, so review does not disappear inside the compiler.

Document every compression like that.

---

## 4. Strict vs quirks plan

List any compatibility behavior that will exist.

| Behavior | Why needed | Strict disposition | Quirks disposition | Evidence row / finding | How it will be tested |
|---|---|---|---|---|---|
|  |  | block / fail / warn | proceed / warn / block |  |  |

Rule: compare-only normalization must not leak into replay semantics.

Rule: strict and quirks modes may change disposition, but not evidence
visibility. Unsupported, skipped, rejected, failed, and unresolved rows remain
in row outputs and `findings.jsonl`.

---

## 5. First MVP and full-ideal target

### First MVP
State the smallest end-to-end claim the frontend will make.

### Near-term upgrade
What is the next phase boundary after MVP?

### Full LawVM ideal
Describe the final intended shape:
- source-honest base seed,
- official amendment semantics,
- explicit canonical effects,
- replay with typed adjudications,
- independent verification,
- historical rebuild plan where needed.

---

## 6. Graduation gates

The jurisdiction cannot claim the following until these are true.

### “Current IR supported”
- P1–P3 artifacts exist
- structure fixtures pass
- provenance is archived

### “Official-act lowering supported”
- P4–P7 artifacts exist
- clause/payload/effect fixtures pass
- adjudications separate unsupported from supported

### “Replay supported”
- P8 exists
- replay skips are typed
- invariants are enforced
- operation/effect rows preserve accepted, rejected, unsupported, skipped, and
  failed statuses

### “Verified replay supported”
- P9 exists
- divergence partition distinguishes source-sparse vs replay-defect
- benchmarks use independent oracle
- evidence-pack summary separates claim rows from non-claim rows

### “Historical replay supported”
- P10 strategy exists
- contamination and base-recovery lanes are explicit

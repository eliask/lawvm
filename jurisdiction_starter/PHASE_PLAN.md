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
| P3 Current source-tree / IR parse |  |  |  |  |
| P4 Official-act parse |  |  |  |  |
| P5 Clause surface |  |  |  |  |
| P6 Payload surface |  |  |  |  |
| P7 Canonical effects |  |  |  |  |
| P7.5 Dry-run vs oracle |  | per-family dry-run-vs-oracle proof + typed refusals |  |  |
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
### P7.5
Dry-run gate (proved mandatory by the New Zealand build). Before actual replay
of an operation family, apply the family's candidate operations to an immutable
parsed *before* tree, materialize a candidate *after* tree, and compare it to
the archived before/after oracle with a mutation-boundary proof and typed
refusals. Reuse `core/mutation_boundary_proof.py` and `core/agreement_residual.py`.
The oracle is a witness, not ground truth: residuals carry a disposition
(`lawvm_wrong` / `oracle_suspect` / `missing_source`); never silently repair to
match the oracle. Actual replay (P8) for a family stays `blocked` until this
surface agrees with the oracle. See `src/lawvm/new_zealand/dry_run*.py`.
### P8
### P9
### P10
### P11

---

## 2.1 Corpus acquisition sidecars

If the jurisdiction has a corpus API, feed, registry export, or local source
repository, declare these acquisition-sidecar artifacts even if they are not
LawVM replay phases:

| Sidecar | Required when | What it proves | What it must not claim |
|---|---|---|---|
| `acquisition_frontier_state.json` | long-running or rate-limited sync | which pages, works, versions, or dependencies were requested and what remains | semantic replay success |
| `acquisition_diagnostics.jsonl` | any live acquisition | request failures, rate-limit waits, unavailable artifacts, schema drift, and beta API gaps | that failed or skipped sources are harmless |
| `dependency_report.json` | source has amendment/effect/history links | source-declared dependency candidates and unresolved edges | that dependency edges are canonical operations |
| `source_tree_summary.json` | current or historical XML/HTML can be parsed structurally | source shape, labels, headings, text, deletion markers, and history witnesses | that the structure is an executable history |
| `snapshot_diff_report.json` | multiple consolidated versions are available | source-visible changes between versions | that a change was produced by a known amendment operation |

These sidecars are especially relevant for New Zealand-like frontends where API
metadata, XML history notes, and consolidated versions can bootstrap source
closure before clause/effect replay exists.

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
- P7.5 dry-run-vs-oracle surface exists for the family and agrees with the oracle
  (mutation-boundary proof + typed refusals) BEFORE actual replay is unblocked
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

# U.S. federal statutory phase plan

This file maps the U.S. federal statutory frontend onto LawVM phase contracts
P0–P11. It states what artifact exists, who owns the claim, and what the first
bounded implementation is — not "supported"/"TODO".

The target is a single positive-law title over one Public Law release-point
window (default Title 11 Bankruptcy; fallbacks Title 35 / Title 18), chosen
data-drivenly by amendment-witness count from the OLRC classification tables.

---

## 1. Phase summary

| Phase | Status | Artifact | Owner | First bounded implementation |
|---|---|---|---|---|
| P0 Capability/inventory | `real` | `inventory_manifest.json` plus omitted/skipped rows | `us/inventory.py` | Inventory acquired release-point title XMLs, PLAW XMLs, classification tables for one window; classify each by role + hash |
| P1 Acquisition/archive | `real` | farchive members + `acquisition_frontier_state.json` + `acquisition_diagnostics.jsonl` | `us/acquisition.py` | Resumable fetch of one window's release points + PLAWs + tables via govinfo/OLRC into farchive, timeout-aware |
| P2 Source record | `real` | hashed source records with role + locator | `us/sources.py` | Resolve canonical locators to farchive members; emit content hashes and source-role witnesses |
| P3 Current source-tree / IR parse | `real` | `source_tree_summary.json` | `us/source_tree.py` | Parse a release-point title XML (USLM) into source nodes: title/chapter/section/subsection/.../clause + headings + notes markers |
| P4 Official-act parse | `real` | parsed PLAW structure | `us/grafter.py` | Parse a Public Law USLM XML into its amendment-instruction sections |
| P5 Clause surface | `real` | `clause_surface.json` | `us/grafter.py` | Extract each amendment instruction: target expression, action word, evidence span, confidence |
| P6 Payload surface | `real` | `payload_surface.json` | `us/payload_surface.py` | Extract quoted strike/insert text and inserted-section bodies; preserve raw quoted bytes separately from match-normalized form |
| P7 Canonical effects | `real` | `operation_effect_rows.jsonl` | `us/grafter.py` -> `core/ir.py` | Lower supported families to `LegalOperation`/`TextPatchSpec` with resolved `LegalAddress`; effective date -> `core/temporal.py` ActivationRule |
| P7.5 Dry-run vs oracle | `real` (gate, mandatory) | per-family dry-run-vs-oracle proof + typed refusals | `us/dry_run.py` | One family at a time (start `REPEAL` or `TEXT_REPLACE`): apply candidate op to immutable prior release-point tree, materialize candidate after-tree, compare to next release point with mutation-boundary proof + typed residuals |
| P8 Replay/materialization | `blocked` (per family until its P7.5 agrees) | replay rows | `us/replay.py` | Unblock a family only after its dry-run agrees with the oracle; apply canonical effects with ordering invariants |
| P9 Verification | `later` | `verify_report.json`, `audit_rows.jsonl`, `partition_report.json` | `us/verify.py` | Compare materialized after-tree to next release point; partition divergences |
| P10 Recovery/historical rebuild | `later` | recovery plan + non-positive-law classification mapping | `us/closure.py` / future module | Defer: non-positive-law titles (Act->USC mapping) and pre-2013 Statutes at Large |
| P11 Reporting/work queues | `real` | `findings.jsonl`, `evidence_pack_summary.json`, frontier work queue | `us/evidence.py` | Emit findings JSONL with stable rule ids; claim vs non-claim summary; witness-anchored frontier ranking |

Statuses used: `real`, `synthetic`, `compressed`, `blocked`, `later`.

---

## 2. Per-phase notes

### P0 — Capability/inventory
- Input: acquired farchive members for one window.
- Output: `inventory_manifest.json` + omitted/skipped/blocked rows.
- Row ids and source links: each USC section id and Public Law number linked to
  its artifact hash and role.
- Unsupported/skipped/rejected: PLAW present only as plaintext -> `unsupported`;
  release point missing -> `blocked`; non-positive-law title section ->
  `skipped` (out of first scope) with reason.
- May claim: which units existed and were eligible. May not claim: any operation
  was executable.
- Failure modes: missing prior release point (no honest base); incomplete table
  coverage.
- Required adjudications: role assignment (base/amendment/oracle/witness).
- Exit criterion: every artifact in the window has a role + hash, and every
  classification-table witness has an inventory row (claimable or not).

### P1 — Acquisition/archive
- Input: window definition (Congress, title, prior+next PL pins).
- Output: farchive members + frontier state + diagnostics.
- May claim: bytes were acquired and hashed. May not claim: replay.
- Failure modes: OLRC timeout/truncation; govinfo listing drift.
- Required adjudications: `us_acq_timeout`, `us_acq_truncated`, `us_acq_404`.
- Exit criterion: all base/amendment/oracle/witness artifacts for the window are
  archived and hash-verified, or recorded as failed with a diagnostic.

### P2 — Source record
- Input: farchive members. Output: source records with role + hash + locator.
- Exit criterion: every canonical locator resolves to a hashed member.

### P3 — Current source-tree / IR parse
- Input: release-point title XML. Output: `source_tree_summary.json`.
- May claim: source shape (labels, headings, notes, text witnesses). May not
  claim: this structure is an executable history.
- Failure modes: USLM `<note>` vs operative-body boundary errors.
- Exit criterion: round-trippable node tree with stable addresses for the title.

### P4 — Official-act parse
- Input: PLAW USLM XML. Output: parsed amendment-instruction sections.
- Exit criterion: each amending section of the Act is isolated with its span.

### P5 — Clause surface (real)
- Input: parsed PLAW sections. Output: `clause_surface.json`.
- Each row: target expression as written, action word, evidence span, confidence.
- Unsupported rows: distributive multi-target, table-cell edits -> recorded, not
  dropped.
- Exit criterion: every amendment instruction in the Act has a clause row.

### P6 — Payload surface (real)
- Input: clause rows. Output: `payload_surface.json`.
- Raw quoted bytes preserved separately from match-normalized form (quote/dash/
  NBSP normalization for matching only).
- Exit criterion: every `TEXT_REPLACE`/`TEXT_REPEAL`/`REPLACE`/`INSERT` row has
  its payload (or a `us_payload_missing` finding).

### P7 — Canonical effects (real)
- Input: clause + payload rows. Output: `operation_effect_rows.jsonl`.
- Lower to `LegalOperation`/`TextPatchSpec` with resolved `LegalAddress`;
  effective date -> ActivationRule (`fixed_date` / `pending_condition`).
- May claim: a closed canonical op was produced. May not claim: it replayed.
- Exit criterion: supported families lowered to closed ops; everything else a
  typed `unsupported`/`rejected` row.

### P7.5
Dry-run gate (proved mandatory by the New Zealand build). Before actual replay
of an operation family, apply the family's candidate operations to an immutable
parsed *before* tree (the **prior** USC release point), materialize a candidate
*after* tree, and compare it to the archived next-release-point oracle with a
mutation-boundary proof and typed refusals. Reuse
`core/mutation_boundary_proof.py` and `core/agreement_residual.py`. The oracle is
a witness, not ground truth: residuals carry a disposition
(`lawvm_wrong` / `oracle_suspect` / `missing_source`); never silently repair to
match the oracle. Actual replay (P8) for a family stays `blocked` until this
surface agrees with the oracle. Start with `REPEAL` (cleanest: tombstone a
section) and `TEXT_REPLACE` (most common, exercises payload-quote normalization),
mirroring NZ's first two families. See `src/lawvm/new_zealand/dry_run*.py`.

### P8 — Replay/materialization (blocked per family)
- Input: closed ops whose family passed P7.5. Output: replay rows.
- Mutation-boundary invariant enforced; out-of-bound mutation -> finding, never
  a silent success.
- May not claim: replay for a family before its dry-run agrees with the oracle.

### P9 — Verification (later)
- Input: materialized after-tree + next release point. Output: audit +
  partition reports.
- Partitions: consistent / replay_defect / compare_shape_only / source_sparse /
  untouched_drift / blocked / skipped / rejected / error.

### P10 — Recovery/historical rebuild (later)
- Non-positive-law title Act->USC classification mapping; pre-2013 Statutes at
  Large PDF recovery. Both explicit deferred lifts with their own contamination
  lanes.

### P11 — Reporting/work queues (real)
- `findings.jsonl` (stable rule ids), `evidence_pack_summary.json` (claim vs
  non-claim counts), witness-anchored frontier ranking by unsupported-family
  witness count.

---

## 2.1 Corpus acquisition sidecars

| Sidecar | Required when | What it proves | What it must not claim |
|---|---|---|---|
| `acquisition_frontier_state.json` | always (window sync over slow OLRC endpoints) | which release points, PLAWs, and tables were requested and what remains | semantic replay success |
| `acquisition_diagnostics.jsonl` | always | OLRC timeouts/truncations, govinfo 404s, listing drift | that failed/skipped sources are harmless |
| `dependency_report.json` | always (classification tables declare PL->USC edges) | which Public Laws touch the title in the window; unacquired-PL edges | that a classification edge is a canonical operation |
| `source_tree_summary.json` | always (USLM is parsed structurally) | release-point title shape: labels, headings, notes, deletion markers | that the structure is an executable history |
| `snapshot_diff_report.json` | always (two release points straddle the window) | source-visible added/removed/changed USC paths between release points | that a change was produced by a known amendment operation |

These bootstrap source closure before clause/effect replay exists: the
classification tables give the witness set, the two release points give the
before/after pair, and the snapshot diff gives an early witness of where the
title moved — none of which is itself a replay proof.

---

## 3. Phase compression rules

No phase is compressed in the first MVP. The Public Law contains structured
amendment-instruction *prose*, but the prose is not a closed canonical op, so
P5/P6/P7 do real lowering work and each emits a real, inspectable artifact
(`clause_surface.json`, `payload_surface.json`, `operation_effect_rows.jsonl`).

The witness denominator from classification tables is **not** a compression of
P7 — it is an independent coverage anchor (see EVAL_PLAN north-star). A
classification-table edge is never substituted for a lowered operation.

---

## 4. Strict vs quirks plan

| Behavior | Why needed | Strict disposition | Quirks disposition | Evidence row / finding | How it will be tested |
|---|---|---|---|---|---|
| Quote/dash/NBSP normalization for strike-text matching | USLM quoting drifts from USC body typography | match-normalize for comparison only; block if match still fails | proceed with normalized match, record recovery | `us_payload_quote_drift` | fixture with smart-quote strike text vs straight-quote body |
| Editorial-note stripping in oracle projection | USC interleaves `<note>` with operative body | strip in compare projection only; never in replay | same | `us_editorial_note_wrapper` | fixture section with effective-date note |
| Effective date silent -> enactment | some Acts omit an explicit date | block (unresolved) | proceed defaulting to enactment, record | `us_effective_date_defaulted_to_enactment` | fixture Act with no effective-date clause |
| Release-point straddle by PL number | pins are PL numbers, not dates | block if before/after do not straddle the amending PL | proceed with nearest straddle, record | `us_release_point_misstraddle` | fixture window with a gap in release points |

Rule: compare-only normalization (quote normalization, note stripping) must not
leak into replay semantics. Rule: strict and quirks change disposition, not
evidence visibility — unsupported/skipped/rejected/failed/unresolved rows stay in
row outputs and `findings.jsonl` in both modes.

---

## 5. First MVP and full-ideal target

### First MVP
Replay `REPEAL` and `TEXT_REPLACE` amendments from a single positive-law
title's Public Law over one release-point window, with the prior release point as
base and the next as oracle, behind the P7.5 dry-run gate. Claim only `replayed`/
`audited` rows.

### Near-term upgrade
Add `REPLACE`, `INSERT`, `TEXT_REPEAL`, `HEADING_REPLACE` families, each behind
its own P7.5 dry-run gate; then P9 full verification with stable partitions.

### Full LawVM ideal
- source-honest base seed: prior release-point USC title XML.
- official amendment semantics: lowered from Public Law USLM XML.
- explicit canonical effects: closed `LegalOperation`/`TextPatchSpec` ops.
- replay with typed adjudications across all observed amendment families.
- independent verification against OLRC release-point oracle, with
  source-sparse vs replay-defect partitioned.
- historical rebuild: non-positive-law title Act->USC classification mapping and
  pre-2013 Statutes at Large recovery, each as an explicit claim regime.

---

## 6. Graduation gates

### "Current IR supported"
- P1–P3 artifacts exist for the title.
- source-tree fixtures pass.
- provenance (release-point hash) archived.

### "Official-act lowering supported"
- P4–P7 artifacts exist for the window's Public Law.
- clause/payload/effect fixtures pass.
- adjudications separate unsupported (distributive/table) from supported.

### "Replay supported"
- P7.5 dry-run-vs-oracle surface exists for the family and agrees with the
  next-release-point oracle (mutation-boundary proof + typed refusals) BEFORE P8
  is unblocked.
- P8 exists; replay skips are typed; invariants enforced.
- operation/effect rows preserve accepted/rejected/unsupported/skipped/failed.

### "Verified replay supported"
- P9 exists; divergence partition distinguishes source-sparse vs replay-defect.
- benchmarks use the independent OLRC release-point oracle.
- evidence-pack summary separates claim from non-claim rows.

### "Historical replay supported"
- P10 strategy exists for non-positive-law titles and pre-2013 recovery.
- contamination (no later-release-point base) and base-recovery lanes explicit.

# U.S. federal statutory frontend — contract-first spec

This directory is the contract-first design spec for the LawVM U.S. **federal
statutory** frontend (package `src/lawvm/us/`). It fills the
`jurisdiction_starter/` templates with concrete, US-specific facts. No code is
implemented here; this is the reviewable build plan that must be coherent before
`src/lawvm/us/` grows.

It is governed by `notes/LAWVM_CONSTITUTION.md`,
`notes/CROSS_JURISDICTION_ARCHITECTURE.md`,
`notes/CORPUS_REPLAY_EVIDENCE_CONTRACT.md`, and
`notes/SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md`. The live exemplar is the New
Zealand frontend (`src/lawvm/new_zealand/`), archetype 4 — the U.S. frontend is
archetype 4 with a positive-law clean-replay core.

## Index

- `JURISDICTION_PROFILE.md` — identity, source families, legal hierarchy
  (title/subtitle/chapter/subchapter/part/section/subsection/paragraph/
  subparagraph/clause), amendment styles, temporal semantics, contamination
  risks, oracle story, first honest target.
- `SOURCE_STRATEGY.md` — source roles, ranking, archival plan with canonical
  farchive locators, raw-vs-derived separation, forbidden shortcuts, minimum
  viable source chain.
- `PHASE_PLAN.md` — P0–P11 mapping with real/blocked/later status, the mandatory
  P7.5 dry-run gate, acquisition sidecars, and graduation gates.
- `ADJUDICATION_PLAN.md` — source-pathology vs compare-shape vs replay-defect
  families, ownership map, verification partitions.

## Decided architecture (encoded, not re-litigated)

US federal law is amendment-replay and fits LawVM well: Public Laws are drafted
as explicit operations ("striking '…' and inserting '…'", "adding at the
end", "is repealed") that map onto `core/ir.py` `StructuralAction`
REPLACE/INSERT/REPEAL/TEXT_REPLACE/TEXT_REPEAL + `TextPatchSpec`. Two addressing
regimes exist: 27 positive-law titles (amendments target the USC directly — clean
replay) and 24 non-positive titles (amendments target the original Act, requiring
OLRC classification-table mapping). **The first proof is positive-law titles
only**; classification mapping is the explicit deferred lift.

## Completion-gate answers

From `jurisdiction_starter/README.md` §"Completion gates":

- **Which source family seeds the base act?** The **prior** OLRC USC
  release-point USLM XML for the target title — the official consolidated Code as
  it stood before the amendment window.
- **Which source family yields amending semantics?** Public Law USLM XML from
  govinfo bulkdata (`us://plaw/{congress}/{plNum}.xml`), whose explicit
  amendment-instruction prose lowers to canonical ops.
- **Which source family verifies replay?** The **next** OLRC USC release-point
  USLM XML for the title — an official point-in-time consolidated oracle. It is a
  witness, not ground truth.
- **Which phases are impossible today, and why?** P10 historical rebuild for
  non-positive-law titles (needs the Act->USC classification mapping, deferred)
  and pre-2013 Statutes at Large (PDF-only, no USLM). P8 replay is `blocked`
  per family until that family's P7.5 dry-run agrees with the oracle.
- **What is the narrowest first replay subset?** `REPEAL` and `TEXT_REPLACE`
  amendments from a single positive-law title's Public Law over one
  release-point window (default Title 11 Bankruptcy; data-driven fallbacks
  Title 35 Patents / Title 18 Crimes; exact title is a fill-in parameter chosen
  by amendment-witness count from the classification tables).
- **What source contamination risks exist?** Chiefly: using a later/current USC
  release point as the pre-amendment base (post-amendment structure leaks
  backward) — forbidden; the before-tree MUST be the prior release point. Also:
  editorial `<note>` wrappers mistaken for operative text; quote/dash/NBSP drift
  in strike payloads; classification-table edges mistaken for enacted operations;
  truncated OLRC downloads stored as partial substrate.
- **What eval would detect architectural cheating?** The eval ladder (see
  `EVAL_PLAN` section of `PHASE_PLAN.md` §4 and the adjudication partitions)
  enforces: (1) the replay base must be the prior release point, never the oracle
  — an anti-cheat test feeds the *next* release point as base and expects failure;
  (2) the same artifact may not be both replay substrate and oracle; (3) the
  witness-anchored coverage denominator comes from OLRC classification tables
  (a fact of the source), not from a candidate-derived count that would inflate as
  extraction improves; (4) unsupported/skipped/rejected rows stay in
  `findings.jsonl` and the evidence-pack non-claim count in both strict and quirks
  modes; (5) the P7.5 dry-run gate proves a family against the oracle with a
  mutation-boundary proof before P8 is unblocked, so no family can claim replay by
  silently matching the consolidated surface.

## Completion-gate answers (§"How to use this folder")

- **Base-source story:** prior OLRC USC release-point title XML (USLM),
  archived at `us://usc/release/{plNNN}/title{N}.xml`.
- **Amendment-source story:** Public Law USLM XML from govinfo bulkdata,
  `us://plaw/{congress}/{plNum}.xml`, lowered through real P5/P6/P7 clause/
  payload/effect surfaces.
- **Verification/oracle story:** next OLRC USC release-point title XML;
  end-state verification, witness not ground truth, residuals carry
  `lawvm_wrong`/`oracle_suspect`/`missing_source`.
- **Local archive/clone/fixture/manifest as replay substrate:** farchive members
  for the window (release points, PLAWs, classification tables); replay/audit
  consume local substrate only, live network is acquisition only.
- **Inventory manifest before any replay claim:** `inventory_manifest.json` (P0),
  with omitted/skipped/blocked rows.
- **Where unsupported/skipped/rejected rows are preserved:**
  `operation_effect_rows.jsonl`, `inventory_manifest.json`, and `findings.jsonl`,
  with run-summary counts; never hidden in either mode.
- **Where findings JSONL is emitted and stable rule ids:** `findings.jsonl` (P11);
  ids include `us_payload_quote_drift`, `us_editorial_note_wrapper`,
  `us_effective_date_unresolved`, `us_release_point_misstraddle`,
  `us_classification_mapping_required`, `us_distributive_target`,
  `us_dependency_unacquired`, plus shared replay-defect ids.
- **Which phases are real/compressed/synthetic/blocked:** P0–P7, P7.5, P11 are
  `real`; P8 is `blocked` per family until its dry-run agrees; P9, P10 are
  `later`. No phase is compressed (the Public Law prose is not a closed op, so
  lowering is genuine work).
- **First executable artifacts:** `inventory_manifest.json`,
  `source_tree_summary.json`, `clause_surface.json`, `payload_surface.json`,
  `operation_effect_rows.jsonl`, then the P7.5 dry-run report for `REPEAL`/
  `TEXT_REPLACE`.
- **What "replay-capable" means here:** for a given amendment family, the P7.5
  dry-run applies candidate ops to the prior release-point tree and agrees with
  the next release point under a mutation-boundary proof with typed refusals;
  only then is P8 replay for that family unblocked.
- **Evidence-pack summary separating claimed from non-claimed rows:**
  `evidence_pack_summary.json` counts `replayed`/`audited` claims separately from
  `unsupported`/`skipped`/`rejected`/`failed`/`unresolved`/`blocked`.
- **Evidence that a divergence is source-sparse rather than a replay bug:** a
  divergence is `source_sparse` when no straddling release point or no acquired
  PLAW exists for the edge; `untouched_drift` when the differing USC path was not
  targeted by any window op; `replay_defect` only when a replay-owned adjudication
  fired or an invariant broke (after editorial-note and quote-drift projection).
- **Acquisition resume / rate limits / frontier state:**
  `acquisition_frontier_state.json` tracks fetched/pending/failed release points,
  PLAWs, and tables; `acquisition_diagnostics.jsonl` records OLRC
  timeouts/truncations and govinfo 404s; OLRC endpoints are mirrored via govinfo
  with bounded retry and hash-verified resume.
- **Source-complete tight-loop corpus / excluded partitions:** a stratified
  source-complete set of supported-family amendments for the chosen title window
  (base + PLAW + oracle + table all present and hashed); rows missing a truth
  surface go to `pending` / `source_sparse` / `notruth`, never mixed into the
  tight-loop score.
- **CLI tools to plan:** inventory, corpus curation, bench history/compare/
  regression, per-row source dump, operation/effect inspection, phase diagnosis,
  bisect/blame, frontier ranking, evidence export, and compact non-interactive
  structural review — following `notes/JURISDICTION_CLI_TOOLING_CONTRACT.md`, with
  `--corpus` selecting the curated US corpus input.

## Status

Spec only. The next step is the `src/lawvm/us/` module skeleton
(`sources.py`, `inventory.py`, `evidence.py`, `acquisition.py`, then
`source_tree.py`, `grafter.py`, `dry_run.py`) per `jurisdiction_starter/
FILE_MAP.md`, reusing the shared `core/` proof-surface objects before writing any
local report/agreement/replay shapes.

# jurisdiction_starter assessment

Verdict date: 2026-06-14. Reviewer: automated assessment against current master
(`e8034d02`) using the just-completed New Zealand frontend as the freshest ground
truth for "how a LawVM jurisdiction is actually started and grown now".

## TL;DR

**Recommendation: UPDATE-IN-PLACE (targeted), not rewrite, not delete.**

The starter's *doctrine* is still correct and, in places, prophetic — it
anticipated almost everything the NZ build later proved (inventory-first, honest
non-claims, oracle-is-not-substrate, typed adjudication families, synthetic
waists, API-acquisition-is-not-replay, source-complete tight-loop corpus). The
NZ build is essentially a faithful *execution* of this starter's archetype-4
("API/feed-backed corpus"). That is strong evidence the starter is useful, not
obsolete.

What is **stale** is that the starter was last touched 2026-05-22, before three
things landed that are now central:

1. The **proof-surface object grammar** (`notes/LAWVM_PROOF_SURFACES.md` §2:
   `SourceWitness -> Claim -> ExecutionAuthorization -> Proof -> Materialization
   -> Agreement -> Residual/FrontierWorkItem`) and its concrete core modules
   (`source_witness.py`, `mutation_boundary_proof.py`, `agreement_residual.py`,
   `proof_surfaces.py`, `frontier_work_item.py`, `evidence_surface_report.py`,
   `frontend_phase_surface.py`, …). The starter never names these. Its `FILE_MAP`
   teaches a from-scratch `sources.py / grafter.py / replay.py / verify.py`
   layout and never says "reuse these shared core objects first" — yet NZ imports
   exactly those core modules.
2. The **dry-run-before-replay discipline** NZ proved end-to-end (per-operation
   mutation-boundary proof against a before/after oracle, typed refusals, actual
   replay stays blocked until dry-run-verified). The starter has P8 "Replay
   MVP" go straight from canonical effects to replay with no dry-run gate. The
   word "dry-run" does not appear anywhere in the starter.
3. The **pinned coverage north-star** (denominator = ground-truth operation
   witnesses, not candidate-derived) and the **spec-ledger / discovered-spec**
   frame (`src/lawvm/tools/spec_ledger.py`, every behaviour a named falsifiable
   rule hypothesis). The starter's EVAL_PLAN has good metrics but no monotone
   witness-denominator north-star and no "discovered spec, disposable code"
   framing.

None of these gaps make the starter *misleading* in a way that breaks a build —
a frontend author who follows it will produce honest artifacts. But they will
**re-derive the shared core from scratch and skip the dry-run gate**, which is
exactly the drift the starter's AI_AGENT_PROTOCOL warns against ("forced to
re-derive the architecture from exemplars and will drift").

## The runtime piece is live — do NOT delete the directory

`p5_runtime_scaffold.py` is not documentation. It is an importable runtime
dependency of `python -m lawvm.tools.scaffold` (the `lawvm scaffold <jur>`
generator), and it is covered by two passing test files:

- `tests/test_jurisdiction_starter_p5_runtime_scaffold.py`
- `tests/test_scaffold_tool.py` (imports `jurisdiction_starter.p5_runtime_scaffold`)

Both pass (9 tests). `ruff check` on the scaffold is clean. It imports cleanly
and is self-contained (no core imports) — which is itself a minor tell: it
predates the proof-surface core objects, so its blocked-P5 row shape is bespoke
rather than a `frontend_phase_surface` / `EvidenceSurfaceReport`. That is
acceptable for a transparency bridge, but a future cleanup could re-express it
on the core objects.

**Deleting `jurisdiction_starter/` would break `lawvm.tools.scaffold` and two
tests.** Deletion is off the table on those grounds alone.

## File-by-file verdict

| File | Verdict | Notes |
|---|---|---|
| `README.md` | accurate, lightly stale | Doctrine sound. Governing-doc list omits `LAWVM_PROOF_SURFACES.md` and `THEORY_OF_LAWVM.md`. No mention of dry-run, the object grammar, NZ-as-exemplar, or reusing core objects. **Updated.** |
| `JURISDICTION_PROFILE.md` | accurate | Pure jurisdiction-fact template; jurisdiction-neutral; nothing has invalidated it. Keep. |
| `SOURCE_STRATEGY.md` | accurate | Strong, anticipates API acquisition exactly as NZ did. Keep. |
| `PHASE_PLAN.md` | accurate but incomplete | P0–P11 phase contracts still a valid mental scaffold (P-labels still used in notes). **Missing the dry-run gate between P7 and P8** that NZ proved mandatory. Graduation gate "Replay supported" should require a passing dry-run-vs-oracle surface first. **Updated** (added dry-run row + gate). |
| `ADJUDICATION_PLAN.md` | accurate, prophetic | Families A/B/C/D map cleanly onto NZ's typed residual taxonomy and `agreement_residual`. Could name the shared core kinds; minor. Keep (left as-is; doctrine correct). |
| `EVAL_PLAN.md` | accurate but incomplete | Eval ladder + tight-loop corpus are right and match NZ. **Missing the pinned witness-denominator north-star** (monotone coverage) and the dry-run eval layer. **Updated.** |
| `FILE_MAP.md` | **stale / mildly misleading** | Teaches an all-local module layout and never says "reuse shared core objects first". A new author will rebuild `agreement_residual`, `mutation_boundary`, evidence shapes that already exist in core. **Updated** (added a "Reuse shared core first" section keyed to PROOF_SURFACES §2; added a `dry_run.py` module to the layout). |
| `API_CORPUS_FRONTEND_ADDENDUM.md` | accurate | Already NZ-aware (names NZ as the archetype). The single most current file. Keep. |
| `ROADMAP.md` | accurate but incomplete | Milestones M0–M8 sound. **M5 "Replay MVP" should be split: dry-run MVP before actual replay.** **Updated.** |
| `TASK_CARD_TEMPLATE.md` | accurate | Generic, still valid. Keep. |
| `REVIEW_CHECKLIST.md` | accurate but incomplete | Missing a dry-run/mutation-boundary check and a "reused core objects?" check. **Updated.** |
| `AI_AGENT_PROTOCOL.md` | accurate | Still correct; its own warning ("re-derive architecture from exemplars and drift") is the exact failure the other stale files now risk. Keep. |
| `p5_runtime_scaffold.py` | accurate, live, tested | Importable, ruff-clean, 9 passing tests, runtime dependency of `lawvm.tools.scaffold`. Keep. Optional future: re-express on core proof-surface objects. |
| `examples/*.json(l)` | accurate | Example evidence surfaces still match the contracts they illustrate. Keep. |

## What is actively harmful (would mislead a new author)

Only one thing rises to "harmful", and it is mild:

- **`FILE_MAP.md` + `PHASE_PLAN.md` together would lead a new frontend author to
  (a) build a bespoke evidence/agreement/replay stack instead of reusing the
  shared core proof-surface objects, and (b) attempt actual replay without a
  dry-run-before-replay gate.** The NZ build proved both are wrong turns. The
  updates below close exactly these two gaps without disturbing the (correct)
  rest of the starter.

## What was changed in this assessment pass

Targeted, reviewable edits only (no rewrites, no deletions):

- `README.md`: governing-doc list now includes PROOF_SURFACES + THEORY; new
  short section "Reuse the shared core, and prove replay with dry-run first"
  pointing at the object grammar, the core module list, the dry-run gate, the
  witness-denominator north-star, and NZ as the worked exemplar.
- `FILE_MAP.md`: new "0. Reuse shared core before writing local modules" section;
  `dry_run.py` added to the recommended layout and implementation order.
- `PHASE_PLAN.md`: dry-run gate added between P7 and P8; "Replay supported"
  graduation gate now requires a dry-run-vs-oracle surface first.
- `ROADMAP.md`: M5 split into M5a (dry-run MVP) and M5b (actual replay MVP).
- `EVAL_PLAN.md`: north-star (witness denominator) metric + dry-run eval layer.
- `REVIEW_CHECKLIST.md`: "reused shared core objects?" and "dry-run /
  mutation-boundary proved before replay?" checks.

All edits are additive; original doctrine text is preserved. The two starter
tests still pass and ruff is clean (no `.py` doctrine files were changed; only
the runtime scaffold was read, not edited).

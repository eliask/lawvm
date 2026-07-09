# Source-Document Two-Level Pipeline — Design Spec

Status: DESIGN (not yet implemented). Consolidates three design passes (Level-1
page simulacra, Level-2 de-facsimile, generalization/module-layout) into one
reconciled architecture + the open decisions to settle before implementation.

The PDF→IR reconstruction is split into two levels with a rich shared metadata
contract between them, and the whole subsystem moves to a neutral `lawvm/ingest/`
home so every jurisdiction can use it.

```
 bytes ──▶ LEVEL 1 (faithful per-page simulacra) ──▶ LEVEL 2 (holistic de-facsimile) ──▶ coherent legal doc IR
            per-page, keep furniture,                cross-page intelligence,
            freeform escape hatches,                 auditable claims, idempotent
            patch-to-convergence
```

Guiding principle (user directive): **always intelligence, never mechanical
heuristics** for the semantic decisions. Mechanics only ever *surface candidates
and metadata*; the model *decides*. Nothing disappears silently — every drop /
join / dedup is a typed, provenance-carrying, reversible claim (AGENTS §1.8).


## 1. Level 1 — faithful per-page simulacra

Each page is transcribed **as it physically is** — furniture (running headers,
page numbers) kept, content split at the page edge kept, no cross-page reasoning.
The simulacrum must match the page image; it is preserved as immutable EVIDENCE.

- **Freeform escape hatch** for content the governed grammar can't hold faithfully
  (math formulas, image-baked text, irregular layout): two new governed kinds
  `MATH` / `VERBATIM` carrying a `V<bbox>` source + inline literal + a closed
  `#reason` vocabulary (`marginalia`/`complex_layout`/`image_baked`/`garbled_source`/
  `ambiguous`/`rotated`/`handwritten`). Bbox-anchored, never pixel-copied,
  rate-limited by construction (clean pages emit zero freeform, stay output-sparse).
  New IR kinds `MATH_REGION` / `VERBATIM_REGION`. This is where the dropped `M_i`
  formula and garbled `∑` get a faithful home.
- **Patch-to-convergence** (per page): `struct_patch` in a bounded loop where the
  model's own prior simulacrum, rendered back to numbered lines, is what it
  patches against the page image. Terminates on empty-patch / idempotent apply;
  guarded against oscillation (structural-hash re-entry), `max_iters` (default 4),
  truncation. Reuses `_apply_patches` verbatim; structural-PATCH (node
  delete/relabel) is a milestone-2 option (see open decisions).
- **Faithfulness = per-region adjudicable claim**: three signals — cross-round
  self-consistency (metadata, not a tier bump), the independent reading-order
  witness (existing `_page_assurance`; freeform regions EXCLUDED from text-witness
  corroboration → default `SINGLE_WITNESS`), and an `unwitnessed_content` tripwire
  that caps duplicated/hallucinated nodes at `UNADJUDICATED_PROPOSAL`.
- **Faithfulness reversal**: Level 1 now **keeps** furniture as
  `role=furniture_hint` nodes (was: dropped at the prompt). "This is furniture" is
  a cross-page judgment → Level 2's call.
- **Interface out**: a `PageSimulacrum` carrier (faithful tree + freeform index +
  convergence metadata + furniture hints + the §3 layout/typography metadata),
  persisted as a per-page evidence record so re-running Level 2 never re-runs the
  model. New `struct_converge` modality with its own cache key.


## 2. Level 2 — holistic de-facsimile (the coherent document composer)

Consumes the stack of page simulacra; emits the coherent whole-document tree PLUS
a `DeFacsimileLedger` of typed claims. Supersedes the mechanical `compose_pages`
stitch (which remains the per-window deterministic fallback when the backend is
down — typed, not a route switch).

Four operations, each an auditable `DeFacsimileClaim` (op + method + tier +
`SpanRef` provenance back to the immutable simulacra + rationale; reversible):

- **DROP_FURNITURE** — running headers / page numbers / footers.
- **REJOIN** — content split across a page/column break (paragraph mid-sentence,
  sentence split, list, table, heading).
- **DEDUP_SEAM** — collapse GENUINE cross-seam duplication while KEEPING
  legitimately-repeated content (a printed table's per-page header, boilerplate).
  Requires **seam-adjacency**, never string-identity; near-duplicate (OCR-variant)
  aware. Legitimate repeats get an explicit `KEEP` claim.
- **REORDER** — coherent cross-page reading order (mostly identity; explicit).

Intelligence decides each **in context**; deterministic affordances (margin-band
position, cross-page recurrence, seam-window shingling, and the §3 metadata) only
*surface candidates*. Applied as a fixed-order deterministic fold over the model's
claims: DROP → DEDUP → REJOIN → REORDER.

- **Idempotence** = the derived doc is a pure fold of (immutable simulacra +
  content-addressed cached ledger). Re-run = cache HIT, byte-identical — inherits
  the determinism-firewall discipline.
- **Context** = 2-page seam windows with 1-page overlap (each seam adjudicated
  once), a carried-open-tail fold for multi-page tables, a cheap global
  furniture-recurrence map as shared context. Windows adjudicate DROP/DEDUP in
  parallel; the REJOIN reduction is a sequential deterministic fold.
- **Validation** = A/B through `fi-parse-compare`: success is EXTRA + STRUCTURE
  findings strictly DOWN, **MISSING not up** (over-dedup guard), **NUMERIC
  unchanged** (never corrupt a euro amount / §). Plus a deterministic
  `verify_ledger` gate: no phantom drops, REJOIN text = exact concatenation,
  body-word-multiset containment.


## 3. Shared metadata contract (the bridge that makes the composer's job tractable)

The richer Level 1 annotates each simulacrum node **deterministically** (free from
pypdfium2 / pdfplumber — the LLM is never asked to *emit* metadata, only to *use*
it), the better Level 2 can adjudicate. Metadata rides on `SourceDocumentNode.attrs`
(the existing extension point) and the anchor's bbox; it is the concrete form of
Level-2's "mechanical affordances surface candidates, intelligence decides".

| class | fields (deterministic) | what it enables in Level 2 |
|---|---|---|
| geometry | bbox, page_w/h, margin-band (top/body/bottom), column index, indent depth, y-order, dist-to-margin | furniture (margin band), column reading order, table geometry |
| typography | font family, size + size-class vs page median, bold, italic, all-caps, small-caps | heading vs body vs caption; furniture (small/caps); dedup (same font at seam) |
| continuation cues | ends-with-terminal-punctuation, starts-lowercase, ends-with-discretionary-hyphen, leading list-marker / section-number | REJOIN split detection; list/section continuation |
| recurrence | cross-page count at same band (global affordance, computed once) | furniture confirmation; artifact-vs-legitimate repeat |
| content hints | numeric-heavy, contains §/citation, is-table-cell, is-image, freeform-reason | protect NUMERIC content; route tables/figures |
| provenance | producer id, per-region assurance signals, convergence flags | tier policy; audit |

Discipline: metadata is captured in the deterministic substrate (`page_elements` +
the pdfplumber profile), attached to nodes, and preserved through composition. It
is an affordance, not authority — a furniture *hint* is confirmed by the model
across pages, not obeyed.


## 4. Home & layout — `lawvm/ingest/` (neutral, NOT core)

The vision ingest is neutral *machinery* that produces the core's neutral
*evidence contracts*; it is neither kernel nor frontend. New top-level
`src/lawvm/ingest/` (sibling of `semantic/`, `substrate/`, `open_law/`):

- **Move (neutral, verified imports)**: `struct_wire`, `page_elements`,
  `adjudicated_ingest`, `parsed_store` (FI default path parameterized),
  `llm_backends/{vision_producer,llm_adjudicator,nemotron_client,docling_producer}`,
  and the neutral `source_document_to_ir_node` split out of `pdf_profiles`.
- **Level 1** lives here (producers under `ingest/llm_backends/` + wire/assembly);
  **Level 2** `defacsimile.py` + `llm_backends/defacsimile_adjudicator.py` land here
  too (NOT `core/source_document/` — the earlier Level-2 draft's placement is
  overridden by this decision).
- **Stays core**: all of `core/source_document/*` (ir, anchors, adjudication,
  extraction, composition, coverage, proposal, validation) — a closed, pure,
  kernel-adjacent evidence waist consumed by both layers. `ingest/` *imports* them.
- **Stays FI (`finland/`)**: `he_draft`, `lausuntopalvelu`, `materialize`,
  `branch_conflicts`, `branch_lowering` (FI-by-role), the FI half of `pdf_profiles`
  (idiom classifier + finlex loader + HE heuristic), `he_acquisition`,
  `qwen_local` (a manual-CLAIMS backend, not vision ingest), and the `tools/fi_*`
  CLIs.

Migration keeps CI green via an `__init__` compat shim, then a single
move+re-register commit, then baseline regen. Enumerated scanner edits:
`inventory_module_roles.py` (`_REPLAY_SCOPE_PREFIX`, `LLM_CLIENT_PREFIX`,
`OPTIONAL_BACKEND_MODULES` must cover `lawvm.ingest.llm_backends.` while keeping
`lawvm.finland.llm_backends.` for qwen); `inventory_architecture_smells.py`
(classify `ingest/` as shared/non-frontend so the hidden-replay-kernel scanner
doesn't police it); `scripts/test_shard.py` (re-register renamed neutral tests);
regen `module_roles_baseline.json` + `replay_coverage_snapshot.json`.


## 5. Decisions (FINAL — Fable-5 deliberation)

1. **Structural PATCH = milestone 2.** Convergence ships text-delta only
   (`_apply_patches`' no-line-shift invariant preserved). Node delete/relabel adds
   a second oscillation axis; the `unwitnessed_content` tripwire (MUST-ship in
   Track B) caps duplicated/hallucinated nodes at `UNADJUDICATED_PROPOSAL` meanwhile,
   and the observed dup class is cross-seam = Level 2's `DEDUP_SEAM`.
2. **Gate convergence to hard pages = YES**, with a closed deterministic trigger
   set on round-1 artifacts: ≥1 freeform region; any `findings`; `patches_applied>0`;
   terminator compliance <0.98; `SINGLE_WITNESS` despite a non-empty reading-order
   witness; truncation. Clean pages stay single-pass. `_page_assurance` runs before
   the gate. Reasons recorded in `ConvergenceInfo.gate_reasons`.
3. **2-page seam windows**; the multi-page-table header boundary IS the fold state:
   a repeated header row is REJOIN-**absorbed** (a `REJOIN.absorbed` sub-claim) iff
   it sits in a table a REJOIN joins to the carried-open table across that seam;
   everywhere else is `DEDUP_SEAM`/`DROP_FURNITURE`. Exactly one claim owns each
   node → `verify_ledger` enforces claim-disjointness; fold-order can't matter.
4. **Model-only edit tier**: `MULTI_WITNESS_ADJUDICATED` only when a deterministic
   affordance (margin-band hit OR recurrence≥threshold) INDEPENDENTLY FIRES on the
   same node (corroboration = output-agreement of independently-produced signals,
   per `_page_assurance` precedent where vision consumes reading-order yet stays a
   witness). No firing affordance → `SINGLE_WITNESS` (still applied, reversible,
   counted, guarded by oracle MISSING-not-up). Absence of contradiction ≠ corroboration.
5. **Ledger** = full per-claim list in a sibling content-addressed blob
   `parsed/<digest>/<pipeline>@<version>/defacsimile_ledger.json` (sorted-keys JSON);
   manifest carries only op/tier histograms + SINGLE_WITNESS-drop count + blob
   locator/digest. `ParsedIrStore.put_ledger`/`get_ledger`; `verify_ledger` gates
   the write.
6. **pdf_profiles split = minimal**: Track A moves only `source_document_to_ir_node`
   → `ingest/lowering.py`; the FI `block_classifier` seam is milestone 2.
7. **Metadata pass-1** = geometry (per-line bbox, margin-band, y-order, indent, col)
   + continuation cues + recurrence + content hints + provenance + text-derivable
   caps/title-case, all deterministic. **Deferred to `meta.v2`**: font family / size-class
   / bold / italic (needs a second pdfplumber lane + cross-extractor alignment);
   keys reserved now so Level 2 treats them optional.
8. **Level-2 input = `Sequence[PageSimulacrum]`**; `compose_pages` stays UNCHANGED,
   invoked as the fallback via `[p.nodes for p in simulacra]` and recorded as a typed
   `method="deterministic_fallback"` claim (not a route switch).
9. **Metadata carrier = in-band `attrs` (str→str)** under a closed namespaced key
   vocab, with a typed `NodeMetadata` codec in `ingest/metadata.py` (frozen in A).
   No sidecar (`SourceDocumentNode` has no identity field). Per-node bbox lives in
   the anchor. Furniture hint key = `hint.furniture` (NOT `role=` — taken by images).
10. **Convergence fixpoint** = SHA-256 over the canonical **resolved** tree (post-PATCH,
    post-span-copy), not the raw wire. Terminations: empty_patch / fixpoint /
    oscillation (earlier-round re-entry; keep last, flag, no tier effect) / max_iters=4.
    Ledger `SpanRef`s address ONLY the final persisted simulacrum; intermediate
    rounds are debug evidence.
11. **Persistence**: per-page simulacra at `parsed/<digest>/<pipeline>@<L1-version>/page/NNNN`
    (`page_simulacrum` class); Level-2 record keeps `parsed_ir_locator` shape under
    `<L1-version>+defacsimile.v1+<adjudicator-id>`; re-running Level 2 reuses cached
    simulacra (never re-runs vision).
12. **Composed tier** = existing `_weakest`-of-parts for merged nodes (unchanged);
    the claim carries its own tier (Decision 4). No new tier algebra.

`struct_converge` modality tag:
`+wire=structbuild.v1+leaf=patch+converge.v1+gate=hard.v1+iters=4+structpatch=text.v1+rasterdpi=200`.


## 5.5 FROZEN interface carriers (locked at end of Track A; B & C compile against these)

```python
# lawvm/ingest/simulacrum.py
@dataclass(frozen=True, slots=True)
class SpanRef:                       # ledger → immutable evidence addressing
    page_num: int
    node_path: Tuple[int, ...]       # child-index path into PageSimulacrum.nodes (tree is frozen)

@dataclass(frozen=True, slots=True)
class FreeformRegion:
    node_path: Tuple[int, ...]
    kind: str                        # "math" | "verbatim"
    reason: str                      # marginalia|complex_layout|image_baked|garbled_source|ambiguous|rotated|handwritten
    bbox: Optional[BBox]

@dataclass(frozen=True, slots=True)
class ConvergenceInfo:
    rounds: int
    round_hashes: Tuple[str, ...]    # canonical resolved-tree hash per round (Decision 10)
    termination: str                 # empty_patch|fixpoint|oscillation|max_iters|gated_single_pass|truncated
    gate_reasons: Tuple[str, ...]    # Decision 2 closed trigger set
    patches_total: int

@dataclass(frozen=True, slots=True)
class PageSimulacrum:
    page_num: int
    nodes: Tuple[SourceDocumentNode, ...]   # faithful page tree, furniture kept (hint.furniture=1), attrs = metadata v1
    freeform: Tuple[FreeformRegion, ...]
    convergence: ConvergenceInfo
    assurance: AssuranceTier                # page-level _page_assurance result
    raw_wire_digests: Tuple[str, ...]

# lawvm/ingest/defacsimile.py (carriers frozen in A; logic is Track C)
class DeFacsimileOp(Enum):
    DROP_FURNITURE; DEDUP_SEAM; REJOIN; REORDER; KEEP

@dataclass(frozen=True, slots=True)
class DeFacsimileClaim:
    op: DeFacsimileOp
    targets: Tuple[SpanRef, ...]
    tier: AssuranceTier
    corroborating_producers: Tuple[str, ...]   # ("defacsimile_adjudicator","affordance:margin_band",...)
    absorbed: Tuple[SpanRef, ...] = ()         # REJOIN header-absorb (Decision 3)
    method: str = "model_adjudicated"          # | "deterministic_fallback"
    rationale: str = ""

# lawvm/ingest/metadata.py — NodeMetadata dataclass + encode/decode ↔ attrs, closed key vocab (Decision 9):
#   meta.v=1 · geom.band=top|body|bottom · geom.col · geom.indent · geom.y_order
#   typo.caps=1 · cue.ends_terminal=1 · cue.starts_lower=1 · cue.hyphen_tail=1
#   cue.list_marker=<m> · cue.section_number=<label> · rec.band_count=<int>
#   hint.numeric=1 · hint.section_ref=1 · hint.furniture=1 · freeform.reason=<vocab>
#   prov.producer · prov.converged   (reserved v2: typo.font, typo.size_class, typo.bold, typo.italic)
```

Also frozen in Track A: additive core enum values `SourceDocumentNodeKind.MATH_REGION`
/ `VERBATIM_REGION` (one closed change to `core/source_document/ir.py`) + their
`source_document_to_ir_node` kind-map entries.


## 6. Implementation tracks (isolated worktrees, pinned base, no push)

- **Track A — move + interface freeze (FIRST, serial).** (1) `lawvm/ingest/` move
  per §4 with `__init__` compat shim; minimal pdf_profiles split (Decision 6);
  scanner edits (`_REPLAY_SCOPE_PREFIX`/`LLM_CLIENT_PREFIX`/`OPTIONAL_BACKEND_MODULES`
  in `inventory_module_roles.py`; frontend-root set in `inventory_architecture_smells.py`
  so `ingest/` is shared/non-frontend; `scripts/test_shard.py` re-registration);
  baseline regen — behavior-identical. (2) Interface-lock commit: `ingest/metadata.py`,
  `ingest/simulacrum.py`, defacsimile claim carriers, the two new IR kinds, hermetic
  codec/carrier tests. **LOCK POINT: §5.5 carriers + key vocab frozen after this.**
- **Track B — Level 1 (parallel with C, on A).** per-line geometry in `page_elements`
  (`PageLine`); MATH/VERBATIM wire kinds + reason vocab + prompts; converge
  orchestrator (gate D2, fixpoint D10, tripwire D1); metadata capture + recurrence
  pre-pass; furniture kept as `hint.furniture`; simulacrum persistence +
  `struct_converge` modality.
- **Track C — Level 2 (parallel with B, on A).** `ingest/defacsimile.py` fold +
  `verify_ledger` (claim-disjointness, phantom-drop, REJOIN-concatenation,
  body-word-multiset, NUMERIC-unchanged); `ingest/llm_backends/defacsimile_adjudicator.py`
  (windowed, line-based, never-JSON, temp=0, index-refs, repetition-guard); ledger
  blob persistence; `compose_pages` fallback adapter (D8); fi-parse-compare A/B.
  **C develops against synthetic `PageSimulacrum` fixtures — no dependency on B's
  impl, only on A's frozen carriers.**
- **Integration (serial, central).** wire `struct_converge` + `defacsimile` versions
  into `resolve_pipeline`; single baseline regen; end-to-end `fi-parse-compare
  --adjudicate` acceptance (EXTRA+STRUCTURE down, MISSING not up, NUMERIC unchanged).

Every track: `isolation: worktree` (MANDATORY — two in-checkout agent mishaps this
session came from omitting it), pinned base commit, no push, cherry-pick + central
baseline regen.


## 7. Milestone 3 — the stigmergic (blackboard) composer

M1 (§2) is one adjudication pass over fixed 2-page seam windows + `verify_ledger`
gate + deterministic fallback. It cannot see structure spanning 3+ pages, cannot
confirm "repeated header = furniture vs. legitimately-reprinted table header"
without watching the recurrence establish itself, and cannot look closer at a
garbled region. M3 replaces the fixed window with a **blackboard**: a shared,
persisted, provenance-carrying workspace that subagents (knowledge sources) read
and post marks to until the composition reaches a fixpoint. This is NOT a chat
agent harness — it is a classic blackboard: bounded-context, single-purpose prompts
coordinating *only* through the workspace, never through conversation.

Governing constraints (from `mekanismirealismi/LLM_USAGE_GUIDE.md`, treated as law
here): **NEVER JSON** — all model I/O is line-based index/span codes, single-letter
internal, expanded to descriptive labels at the serialization boundary; **omit
lines with no finding**, `NONE` for an empty reply; **input-heavy / output-sparse
(>10:1)** — the facsimile wire is near-free input, output tokens are ~40× dearer;
**raise on truncation** (`finish_reason=length` → `LLMContextExhausted`, never a
silent `""`), then scope-reduce; **no GBNF** (generate freely, parse leniently);
**every output verifiable by reference** (a claim/mark cites the SpanRef it acts on).

### 7.1 The blackboard

A per-document workspace keyed by `SpanRef` region, persisted as a content-addressed
journal (sibling of the ledger blob; same determinism-firewall discipline — same
simulacra ⇒ same journal ⇒ cache-HIT byte-identical). Each entry is a typed **mark**
carrying `{region, mark_kind, producer_id, round, evidence_refs, rationale}`.

- **Pre-seeded deterministically** from §3 metadata so no subagent starts blank:
  `rec.band_count ≥ θ` → `FURNITURE?` candidate; `freeform.reason ∈ {math,image_baked,
  garbled_source}` → `GARBLE?` (a VIEW candidate); an open continuation cue-chain
  crossing a page edge → `OPEN` (a REJOIN candidate).
- **Mark kinds** (line-based codes internal):
  - *candidates* — `DROP? DEDUP? REJOIN? KEEP? REORDER?` (proposals, not yet claims)
  - *decisions* — promote a candidate to a `DeFacsimileClaim` (the only marks that,
    once `verify_ledger`-gated, mutate the output tree)
  - *epistemic* — `OPEN` (chain awaiting its continuation), `GARBLE` (needs a visual
    look), `CONTESTED` (two producers disagree), `DEFER` (revisit after more context)
- Marks are append-mostly and reversible (AGENTS §1.8); a decision supersedes its
  candidate but the candidate + its evidence stay in the journal for audit.

### 7.2 Affordances (a typed, extensible dispatch table — control lines the harness acts on)

The affordance set is deliberately **open** (you cannot enumerate every needed
affordance a priori). Each is a control line the deterministic harness dispatches;
adding one is adding a dispatch entry, not reshaping the protocol. Read affordances
acquire context (input side, ~free); write affordances post to the blackboard/ledger
(output side, kept sparse).

**Read (context acquisition):**
- `PAGE <n>` — the token-efficient struct wire of page *n* (the default unit).
- `EXPAND <lo> <hi>` — widen the window by adjacent pages. **Bounded**
  (`max_context_pages ≈ 6`, `max_expansions ≈ 3`). Demand-driven, OR forced
  deterministically when an `OPEN` REJOIN chain crosses the window edge.
- `VIEW <n>` / `VIEW <n> <bbox>` — the **visual** rendering of a page or subregion
  (multimodal). The wire is default; the image is the single most expensive input, so
  it is gated behind an explicit request or a deterministic trigger (a freeform
  `math`/`verbatim` region, or a `verify_ledger`-flagged garble) — never sent by
  default. Bounded (`max_views`). The crop is content-addressed and locatable
  (`<digest>.pdf/NNNN.img`), reusing the inline-image scheme.
- `NOTES <region>` — read the blackboard marks for a region + its neighbours (the
  stigmergy read side; compact, so a subagent sees prior conclusions without re-reading
  raw neighbouring pages).
- `PREFIX` — the settled de-facsimiled document-so-far (tail *K* nodes / a compact
  deterministic summary) as running context.

**Write (post to workspace / ledger):**
- `NOTE <region> <mark> [evidence…]` — leave a typed mark (the stigmergy write).
- `DROP|DEDUP|REJOIN|KEEP|REORDER <ids…>` — promote a candidate to a claim (existing
  Track C grammar; `verify_ledger` is the hard gate before any reaches the output).
- `DEFER <region> <reason>` — mark undecided, revisit after more context accrues.

### 7.3 Knowledge-source subagents (heavy, but instrumental)

Each is a fresh bounded-context prompt with **exactly** the context the task needs
(logical-dependency completeness), `temperature=0`, `enable_thinking=false`,
line-based output, omit-on-nothing, raise-on-truncation. They never talk to each
other — they read/post marks. A deterministic **controller** (the harness) schedules
the next subagent on the highest-value `UNDECIDED`/`OPEN`/`CONTESTED` region.

| subagent | scope | model tier |
|---|---|---|
| seam adjudicator | DROP/DEDUP/REJOIN over the current window (blackboard-aware) | vision 35B |
| furniture classifier | a recurring band: furniture vs. legit repeat (binary, yes/no per band) | 9B-class |
| visual transcriber | a `VIEW`'d garble/formula region → faithful text | vision, narrow |
| chain closer | does page *n+k* continue this `OPEN` REJOIN chain? | targeted |
| contest resolver | two disagreeing marks → decide, citing evidence | vision 35B |

### 7.4 Convergence (stigmergic fixpoint)

Loop: controller picks the next undecided region → dispatches the right subagent with
its bounded context → the subagent reads marks, optionally requests `VIEW`/`EXPAND`,
posts marks/claims → repeat. **Terminate** when a full sweep adds no new marks and
leaves no `UNDECIDED`/`OPEN`/`CONTESTED` region (fixpoint), OR the budget
(`max_rounds` / `max_views` / token budget) is exhausted → decide the residue with
what is visible, typed `context_exhausted` (falling back to the deterministic
`compose_pages` claim for that region, never a silent drop). `verify_ledger` gates the
emitted ledger exactly as in M1 — a failed model ledger never reaches the output.

### 7.5 Token economy & determinism firewall

- Wire is input-heavy/near-free; images gated (§7.2); the blackboard IS the
  shared-context compression — subagents read compact marks, not raw neighbours,
  unless they `EXPAND`. Recurrence map + prefix summary are computed once
  (deterministic Phase 1) and fed compactly, not re-derived per call.
- Truncation → `LLMContextExhausted` → scope-reduction hierarchy (window → single
  seam → single node), aligning the existing `AdjudicationTruncated` fallback to the
  guide's Class-2 discipline.
- Firewall preserved: reads / notes / views / expands are **epistemic** — they change
  only what the model sees or leave advisory marks. The *only* thing that mutates the
  output document is a claim that passes `verify_ledger`. The journal is
  content-addressed → reproducible, cache-HIT byte-identical.

### 7.6 Sequencing & open decisions

- **Builds on #237** (de-facsimile conservatism tuning): a conservative adjudicator
  that can *ask for more* (VIEW/EXPAND) before committing strictly dominates one that
  is conservative because it is half-blind. M3 lands **after** #237 merges — it edits
  `defacsimile.py` + `defacsimile_adjudicator.py` (both #237-owned) plus new
  `ingest/blackboard.py` (journal + mark codec + controller), `parsed_store` (journal
  persistence), and `metadata.py` (VIEW crop locators). Modality tag gains
  `+compose=blackboard.v1+maxviews=<k>+maxctx=<p>`.
- **Open — settle before impl:** (1) blackboard journal schema (line-based mark wire
  vs. a frozen `WorkspaceMark` codec — likely both, wire for the model, codec for
  persistence); (2) controller scheduling policy (priority over region kinds:
  `CONTESTED` > `OPEN` > `GARBLE` > candidate?); (3) `θ` recurrence pre-seed threshold
  and `max_views`/`max_ctx` budgets (tune on the HE corpus A/B); (4) whether the
  contest-resolver is a distinct tier or MULTI_WITNESS by construction (two producers
  already disagreed → resolution is adjudication, not a fresh witness).


## 8. Milestone (bilateral) — Level-1 agentic re-read & the shared visual affordance

**Observed defect (real, HE 2015/1 p4n11):** the Level-1 vision model can emit
*confidently garbled* OCR (`sopimusekertaluont-eestisaat…`) as ordinary text — it is
NOT flagged `freeform.garbled_source`, so it looks clean. Level-2 then faithfully
carries the garbage through. **Level-2 conservatism structurally cannot fix a Level-1
read defect** (#237 correctly ruled it out of scope). The fix must happen where the
mis-read is produced. This makes the "re-view a page / bbox, zoom, re-read carefully"
affordance **bilateral** — needed at Level 1 (repair the read) as well as Level 2 (VIEW
during composition, §7.2).

- **Shared visual primitive** — new `ingest/visual.py`: `render_region_crop(manifestation,
  page_num, bbox, dpi) -> bytes` (content-addressed, locator `<digest>.pdf/NNNN.img`).
  ONE implementation consumed by BOTH the Level-1 re-read below and the Level-2 `VIEW`
  affordance. Build it standalone.
- **Suspect-region surfacing (deterministic — surfaces candidates, model decides):**
  primary signal = **cross-reader disagreement** — an independent reader (pdfium text
  layer, docling, or nemotron) over the same bbox disagrees with the vision read;
  secondary = cheap lexical implausibility (OOV/char-n-gram score, long space-less alpha
  runs, degenerate vowel ratio). Regional divergence of the reading-order witness (a
  *localized* `_page_assurance`) also triggers. None of these EDIT anything — they only
  mark a region for the model to re-read.
- **Re-read action (agentic):** in/after the converge loop, `render_region_crop` the
  suspect bbox at higher DPI and re-read **just that region** (`vision_producer.reread_region`),
  replacing the suspect leaf via the EXISTING patch mechanism iff the re-read is more
  plausible / agrees with a cross-reader. Rides the existing convergence fixpoint +
  assurance gates — firewall preserved: a re-read mutates the simulacrum only through a
  normal, already-gated patch; it is never authority.
- **Multi-reader cross-witness → regional tier:** where an independent reader
  *corroborates* the re-read, that region earns genuine multi-witness assurance (not just
  the page-level default) — the reading-order witness becomes regional, not whole-page.
- **Docling / alternate read backends** already exist under `ingest/llm_backends/`
  (`docling_producer`, `nemotron_client`) — this milestone wires them in as the
  independent-reader lane rather than a mere fallback.
- **Determinism / output-sparsity:** clean pages surface zero suspects → zero re-reads
  (input-heavy/output-sparse holds); the re-read is content-addressed → reproducible,
  cache-HIT byte-identical. Recorded in `ConvergenceInfo` (additive: reread count +
  a `suspect_region` gate reason).


## 9. Level-1 region-decomposition reading (proactive bbox tiling + overlap-stitch)

A whole-page vision read is simply the **coarsest tiling** — and the lossiest. On dense,
multi-column, or table-heavy pages a single read is overly difficult and drops/merges
content (garbles, lost cells, merged columns). When the deterministic layout already
resolves the page into coherent regions (columns, blocks, paragraphs, table cells,
figures — all available from `page_elements` geometry + the §3 metadata `geom.col` /
`geom.band` / block structure), reading each region on its **own crop** is more faithful
and lets the model concentrate. §8 (reactive re-read of a suspect region) and §9
(proactive up-front subdivision) are therefore ONE mechanism at different granularities;
the whole-page read is the 1-region degenerate case.

- **Deterministic region proposal (surfaces candidates; the model reads):** from the
  `page_elements` geometry, propose read regions at SEMANTICALLY-COHERENT boundaries
  (block / column / cell / figure) — never arbitrary pixel tiles. Regions MAY deliberately
  **overlap** at boundaries so no content is cut at a seam.
- **Per-region read:** each region → `render_region_crop` (the §8 primitive) → a bounded
  region read. Regions are far more self-contained than a page, so intra-page region reads
  can **parallelize** (bounded, GPU-saturating). This lifts the original "per-page, not
  per-region" concurrency bar (§ pipeline concurrency): the cross-region structure now
  comes from the deterministic layout + the stitch, NOT the model's running context — so
  the reason per-page parallelism was withheld no longer applies at region granularity.
- **Overlap-stitch via span-copy:** overlapping region reads are stitched into the page
  tree by deduplicating the overlap with the SAME near-duplicate / span-copy machinery as
  Level-2 `DEDUP_SEAM`/`REJOIN`, but INTRA-page; continuation cues
  (`cue.ends_terminal`/`starts_lower`/`hyphen_tail`) drive the join. The overlap is
  redundancy → a cross-read **witness**: agreement in the overlap corroborates (regional
  multi-witness tier), disagreement localizes a suspect region (→ the §8 re-read).
- **Adaptive default (output-sparse, cost-proportional):** clean single-column pages stay
  whole-page (one region); a page subdivides only when a deterministic complexity signal
  fires — multi-column, table density, region-count over threshold, oversized page. Most
  pages pay nothing.
- **Firewall preserved:** region reads assemble the simulacrum only through the normal
  gated tree build; the overlap-stitch is deterministic; nothing bypasses the convergence
  fixpoint / `_page_assurance`. Content-addressed crops → reproducible, cache-HIT
  byte-identical.
- **Sequencing:** track **P6**, stacks on P5 (reuses `ingest/visual.py` + the region-read
  + the intra-page span-copy stitch) and shares `page_level.py`/`vision_producer.py` with
  P5 — so it is Wave 3 (after P5 merges), not parallel to it.


## 10. Review outcomes (Fable-5) — BINDING decisions

Holistic architecture review (Fable-5, 2026-07-10). Verdict: the design is coherent
but had begun to **fork into two adjudication universes** — the new converge/re-read/
de-facsimile machinery was producing/mutating text through bespoke paths while the
mature producer-neutral `core.source_document.adjudication` kernel (Nemotron /
pdfplumber / reading-order witnesses → `ExtractionAssertion` → `assurance_for`) was
sidelined. These decisions unfork it. User-ratified 2026-07-10.

1. **Seam norm (binding):** **Level 1 owns BYTES** (what text a region contains);
   **Level 2 owns ARRANGEMENT** (which nodes survive, in what order). **Level 2 may
   never originate or alter text.** `verify_ledger`'s word-multiset containment is the
   mechanical enforcement — never weaken it. Consequence: **§7.3's "visual transcriber"
   is deleted** (it would emit text present in no simulacrum → `verify_ledger` must
   reject). A L2 `GARBLE` mark is a **finding routed back to Level 1** (§8 re-read →
   new versioned simulacrum → cheap cached fold re-run). The blackboard may DETECT, not
   REPAIR.
2. **Unification (adopted now):** §8/§9 reads and re-reads flow through the EXISTING
   `ExtractionAssertion` → `core.source_document.adjudication` kernel — candidates
   {original vision read, re-read@300dpi, pdfium line, docling/nemotron} adjudicated by
   the core `Adjudicator`; the composed node's tier comes from `assurance_for` over
   **genuinely distinct producers**. "Regional multi-witness tier" is then the existing
   kernel doing its job, not new machinery. Retire `_page_assurance`'s 1500-char
   page-prefix tier → weakest-of-regions. `DeFacsimileClaim` stays (arrangement) but
   never carries corrected text.
3. **§7 blackboard = DEFERRED / shrunk.** Largest surface, smallest measured defect
   class after conservative M1 + §8. Ship only the narrow residual: a **bounded `EXPAND`
   affordance** on the conservative seam adjudicator + the **carried-open-tail fold**
   (3+-page tables) and `CONTESTED` resolution — NOT the full mark-journal /
   subagent-scheduler apparatus. (P1 reshaped in place to this.)
4. **Calibration reframe:** accuracy-vs-granularity is a **U-curve** (coarse → garble/
   truncation; fine → lost linguistic context). Control variables are **pixels-per-glyph**
   and **output-tokens-per-call** cliffs, not region count. Operate at **0.7× the cliff
   load** (that is the home of the user's "−30% margin"). Emit a **deterministic adaptive
   `subdivide(page_elements)` policy** (a pure function of geometry; thresholds folded
   into the modality tag), not a global constant. Score **end-to-end post-stitch,
   NUMERIC-exact primary**; gold = pdfium text layer (born-digital, free per-region,
   geometry-aligned) + authoritative XML (document-level, and the only gold for scanned
   pages). Validate the oracle-free proxies (overlap/cross-reader disagreement rates)
   against gold in the SAME run — the experiment's real product is a validated monitoring
   instrument, not a constant.
5. **Modality:** reject "N adjacent crops → one joined-text call" as a primary regime
   (breaks verifiability-by-reference; output-heavy; one bad crop poisons the call).
   Use the hybrid the design already implements: read images per-region, make the JOIN
   decision over TEXT. Multi-crop only as a bounded, anchored seam/re-read escalation
   (patch-shaped output).
6. **Overlap:** cut regions at semantic layout-element boundaries (no pixel tiles, no
   mid-line); **1-line corroboration overlap**. **Agreement = self-consistency metadata,
   NEVER a tier bump** (same producer, correlated failures — would debase the assurance
   vocabulary); **disagreement = `SuspectRegion`** (a cheap strong localizer). Genuine
   tier-raising corroboration requires a DIFFERENT producer. Freeze a `RegionRead`
   carrier (region_id, page, bbox, covered element ids, overlap-zone element ids, edge
   cues, col/y-order, crop digest+dpi) before P6.
7. **Determinism pins:** fold a **digest of the prompt-constant set into the version tag**
   (a prompt edit without a tag bump silently changes semantics under the same key);
   **append-only store**, same-key re-run divergence surfaced as a first-class
   self-consistency signal (temp=0 is not bit-stable on llama.cpp) — never a silent
   overwrite; deterministic controller scheduling order with tie-breaks; **merge parallel
   region reads by region id**, never completion order; all budgets count events, never
   wall-clock.
8. **Fix now — real bug:** `page_level._line_index_by_text` binds geometry to nodes by
   exact normalized text, first-wins → any leaf the converge loop or §8 re-read CORRECTS
   loses its `PageLine` bbox (→ un-re-readable, and L2 loses geometry exactly where the
   page is hardest); recurring identical lines all bind to the first occurrence. Bind by
   **source-line index captured at cold-read time**. Prerequisite for §8 to keep working;
   applied to P5 at integration.
9. **Latent fidelity gaps to schedule:** REJOIN `_rejoin_text` single-space join breaks
   discretionary-hyphen seams (`valtio-` + `neuvosto` → `valtio- neuvosto`) while the
   exact-concatenation gate forbids fixing it in the fold → **dehyphenate at L1** (bytes
   are L1's job); document that `verify_ledger`'s numeric/word checks are **global
   multiset (a ratchet, not positional proof)**; delete the dead `ContinuationJudge`
   LLM-substitution hook (superseded by the L2 adjudicator).

### 10.1 Escalation = a condition/restart protocol (all levels) — user directive 2026-07-10

Escalation is not merely an upward-routed record; it is the pipeline's **condition
system**, closer to Common Lisp's conditions-and-restarts (and its reflective
metaobject flavor) than to Python's unwind-the-stack exceptions. The shape:

- **Expectations are an explicit contract handed to each level.** Every producer /
  subagent / level receives the invariants it must uphold and the assumptions it may rely
  on (L1 read: "crop arrives at DPI≥θ; emit only governed kinds; every leaf witnessable";
  L2 composer: "simulacra are faithful; never originate text; `verify_ledger` must pass").
  The contract is **data, declared per level** (the MOP flavor) — the harness dispatches
  generically and new expectation kinds are added declaratively, not by rewriting control
  flow. This is the same "typed, extensible dispatch table" discipline as §7.2.
- **Any violation of the contract — OR any unanticipated concern the typed vocabulary
  can't express — SIGNALS a condition.** This is the catch-all *beyond* the known
  pathology families (`SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC`); it fires on the
  *unexpected*. Signaling does NOT force an unwind — the signaler stays live, carrying its
  state, so a handler can resume it.
- **The signaler offers RESTARTS** — the menu of valid ways to continue from the signal
  point: e.g. `re-read-region-higher-dpi`, `route-to-level-1`, `use-fallback-reader`,
  `mark-unresolved-and-continue`, `abort-region`, `defer-to-human`. Restarts are data too.
- **Handlers up the chain (producer → composer → orchestrator → human) pick a restart.**
  The nearest handler with enough context resolves it; if none can, it propagates upward,
  never silently swallowed. A **human-in-the-loop handler is the first-class TERMINAL
  handler.** The chosen restart resumes the signaler from the signal point — no lost work,
  no blind retry-from-scratch (the Python-exception failure mode).
- **Determinism preserved:** the handler POLICY (which restart for which condition) is
  itself deterministic and versioned where automated, and every `(condition, chosen
  restart, handler)` triple is recorded in the journal — a cache-HIT re-run replays the
  same resolution byte-identically; only a genuinely new / human restart is a new journal
  entry. This **subsumes today's hardcoded fallbacks**: backend-down → `compose_pages`
  becomes the `use-fallback-reader` restart of an `extractor-unavailable` condition,
  making all fallbacks uniform, auditable, and overridable.
- **Relation to existing doctrine:** this is the operational form of "prefer explicit
  pathology/adjudication over silent recovery" — a condition is the explicit signal, a
  restart is the explicit provenance-carrying recovery, and refusal-to-guess is just the
  `mark-unresolved` / `defer-to-human` restart.

The `ESCALATE` mark of §7/P1 is the first concrete instance: it carries origin level /
producer, region / anchor, the violated expectation (or `"unanticipated"`), the signaler
state, **the offered restarts**, and a suggested owner level. Precedent: the
de-facsimile-tuning agent correctly signalled "the garble is a Level-1 read defect, out
of my (Level-2) scope" and offered the `route-to-level-1` restart — that class of signal
must be structural, not incidental.

### 10.2 Sequencing (Fable-5, ratified)

NOW: adopt the seam norm + unification (decisions 1-2); reshape P1 (decision 3); land §8
through the unified shape + the geometry-bridge fix (decision 8). NEXT: the calibration
harness (decision 4) built on the P2 corpus e2e harness, BEFORE §9 implementation —
born-digital pages may already be at ceiling, so calibration decides how much of §9 to
build. THEN: §9 adaptive-only on the frozen `RegionRead` carrier (decision 6). The
minimal accuracy-moving version — L1 converge + §8 re-read with one genuine cross-reader
+ conservative M1 + NUMERIC-exact e2e gate — is ~90% landed; marginal accuracy per hour
is now in calibration + the geometry-bridge fix, not new composition machinery.

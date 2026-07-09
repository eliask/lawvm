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

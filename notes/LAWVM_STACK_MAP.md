> **Status (2026-06-22):** Largely-current, some stale refs. Descriptive (living orientation map). Already self-flags §1/§4 as pre-johtolause-refactor (correct: peg3.py + grafter.py are gone). Additional uncaught drift: §5 cites dead COMPILER_WARNING_AND_INVARIANT_FRONTIER.md (now COMPILER_OBSERVATION_STREAM.md); §6 tree says 'uk/' but the dir is 'uk_legislation/'.

# LawVM Stack Map

Status: living reference.
Kind: descriptive.

Purpose: stable orientation map for the current actual architecture. Read this
first before any LawVM work. Not a target spec — describes what exists now.

> ⚠ STALE — the §1 pipeline diagram and the §4 "where logic belongs" table below
> predate the `johtolause/` clause-surface refactor and the removal of the
> `grafter`. The named modules `johtolause/peg3.py` and `finland/grafter.py` no
> longer exist (the FI surface parse now lives in `finland/johtolause/`, e.g.
> `clause_surface.py`, `lower_surface.py`, `surface_resolve.py`; there is no
> single "central orchestrator" grafter — compilation is staged across
> `finland/compile_group_*.py` and replay across the `finland/apply_*.py`
> family). For the **current, authoritative** waist chain and terminology, read
> `LAWVM_PIPELINE_CONTRACT.md` §2 (the ten full-pipeline waists) and
> `LAWVM_ARCHITECTURE_INDEX.md`. Treat the diagram below as a historical sketch
> until it is regenerated against the contract.

## 1. Pipeline Overview

```
Amendment XML (Finlex Open Data API / Farchive)
  │
  ├── lxml parse (read-only, never mutated)
  │
  ├── Clause-surface parser ─── tokenize → recognize → resolve ──→ surface clause
  │     dir:  finland/johtolause/ (clause_surface.py, lower_surface.py,
  │           surface_resolve.py, lift_to_surface.py — no single peg3.py)
  │     types: finland/johtolause/types.py (LegalAddress et al.)
  │
  ├── Lowering ─── ParsedOp.to_legal_operation() ──→ LegalOperation
  │     file: core/ir.py (LegalOperation, IRNode, xml_to_ir_node)
  │
  ├── Compile / elaboration ──→ canonical ops + constraint predicates
  │     dir:  finland/compile_group_*.py (surface, lowering, elaboration,
  │           scope-recovery stages — replaces the former monolithic grafter)
  │     constraints: finland/constraints.py (filter predicates)
  │     scope: finland/scope.py (chapter/scope inference)
  │
  ├── Payload extraction ─── xml_to_ir_node ──→ IRNode tree
  │     file: core/ir.py (xml_to_ir_node, positional labels)
  │
  ├── Pre-resolve ─── _pre_resolve_omissions ──→ IRNode merge
  │
  ├── Apply ─── tree_ops on replay tree ──→ mutated IRNode tree
  │     file: core/tree_ops.py (pure functional)
  │     ops: replace_at, remove_at, insert_sorted, check_invariants
  │     apply: finland/apply.py + the finland/apply_*.py family
  │     side output: lo_ops_out (section snapshots, post-apply)
  │
  ├── Timeline ─── compile_timelines(base, lo_ops_out)
  │     file: core/timeline.py (~911 lines)
  │
  ├── Materialization ─── materialize_pit(timelines, date, base)
  │     output: master.ir = materialized PIT body
  │     output: master.timelines = compiled timelines
  │
  └── Evidence ─── oracle comparison → adjudication → proof claims
        file: tools/evidence.py, tools/evidence_render.py
        CLI: lawvm evidence-review
```

All paths relative to `src/lawvm/`.

## 2. Key Architectural Properties

**Timeline-primary.** `replay_xml` always compiles timelines and materializes
PIT. `master.ir` is the materialized PIT body — one root (`materialization_root`)
of the certificate dossier that is the actual deliverable (see
`LAWVM_PIPELINE_CONTRACT.md` §9), not the whole output. The replay tree is
internal machinery for address resolution during compilation — never read from
`master.tree` (lxml) after replay.

**Fully synchronous.** No asyncio, no aiohttp, no runtime LLM calls. All
tools are sync. Run from `LawVM/` with `uv run lawvm <cmd>`.

**IRNode-native.** All mutations go through `tree_ops` on IRNode. No lxml
mutations. Positional labels ("1","2","3") assigned in `xml_to_ir_node`.

**Base-template materialization.** PIT body preserves unlabeled content
(cross-headings, liite, voimaantulo) from base body structure via overlay.

## 3. The Three Waists (Current vs Target)

> The three replay-core waists below are the historical subset. The current
> canonical model is the **ten full-pipeline waists** in
> `LAWVM_PIPELINE_CONTRACT.md` §2; the three here map onto its
> surface-syntax / canonical-op / apply-receipt waists.

The archived design memos describe an ideal 3-waist architecture: clause
surface AST -> payload surface IR -> canonical ops.

Current reality: the grafter conflates elaboration, payload normalization,
and some apply-time inference. The three waists exist conceptually but are
not yet clean code boundaries. Specifically:

| Waist | Target | Current |
|-------|--------|---------|
| Clause surface | 5-node AST (RefAmend etc.) | ParsedOp (flat dataclass) |
| Payload surface | PayloadSurface IR | xml_to_ir_node → IRNode directly |
| Canonical ops | Typed CanonicalOp | LegalOperation (close but lacks PathologyIntent) |

The gap is real but narrowing. Each family-fix iteration should move toward
the target boundaries without requiring a rewrite.

## 4. Where Different Kinds of Logic Belong

| Kind | Where now | Where it should be |
|------|-----------|-------------------|
| Surface syntax | johtolause/ recognizers + grammar | johtolause/ (tag not delete) |
| Payload shape | compile_group_lowering payload extraction | separate payload IR (FINLAND_PAYLOAD_IR_SPEC) |
| Elaboration (stateless) | compile_group_elaboration repair/supplement | should be explicit structural elaboration pass |
| Elaboration (stateful) | compile_group_elaboration slot assignment | should use typed constraint problem |
| Replay execution | apply_*.py + tree_ops | correct location, keep boring |
| Invariant checking | tree_ops.check_invariants | correct, extend per REPLAY_INVARIANTS spec |
| Evidence/proof | tools/evidence.py | correct location |

## 5. Observation/Warning Taxonomy

Warnings are emitted at 6 layers, documented in
COMPILER_WARNING_AND_INVARIANT_FRONTIER.md. The rule: emit at the layer that
first knows. Key families:

- **Frontend PEG**: duplicate_target_op, semantic_collapse_move_or_renumber,
  lossy_filter_strip_risk, scope_carry_forward_required
- **Lowering/repair**: weaker_duplicate_target_shadowed, grouped_container_scope_repair
- **Payload**: multiple_plausible_slot_assignments, container_membership_mismatch
- **Apply**: failed_operation, uncovered_body_recovery_required
- **Product**: tree_invariant_violation, replay_product_invariant_violation
- **Evidence**: mixed_replay_risk, source_pathology, oracle_incorrect

## 6. Multi-Jurisdiction Structure

```
core/           — shared IR, tree_ops, timeline, compile
finland/        — FI-specific parser (johtolause/), compile/apply, scope, constraints
estonia/        — EE pipeline (fetch, parse, compile, replay)
uk/             — UK pipeline (parse, amendment replay)
norway/, sweden/, eu/  — early-stage pipelines
tools/          — CLI commands, evidence, rendering
```

Each jurisdiction has its own parser and compile/apply stages. Core provides IRNode,
tree_ops, timeline, and LegalOperation. This is already close to the
JurisdictionPack pattern from the archived universal frontend model.

## 7. Current Accuracy

Do not trust hardcoded accuracy numbers in this map — they drift. For the
current per-jurisdiction scores and what the score means, run the bench and read
`UNIFIED_BENCH_CONTRACT.md`:

```bash
uv run lawvm bench --label vN
```

FI remaining failures are typically invariant-heavy mixed cases rather than
broad replay bugs; the modern-era PROVED_REPLAY_BUG frontier is the live signal,
not the headline percentage.

## 8. Essential Commands

```bash
# Full bench
nice -n 19 uv run lawvm bench --label vN

# Single-statute debug flow
uv run lawvm bisect SID
uv run lawvm explain SID
uv run lawvm diff SID

# Modern mixed-risk frontier
uv run lawvm evidence-review --oracle-corpus --cache-only \
  --mixed-replay-risk-only --min-year 2010 --workers 16
```

Full CLI reference: see `uv run lawvm --help`.

## 9. North Star Documents

The public north-star documents are [LAWVM_CONSTITUTION.md](LAWVM_CONSTITUTION.md),
[THEORY_OF_LAWVM.md](THEORY_OF_LAWVM.md), and
[CROSS_JURISDICTION_ARCHITECTURE.md](CROSS_JURISDICTION_ARCHITECTURE.md).

Current code is pragmatically evolving toward the target architecture. Do not
attempt wholesale extraction while family-specific correctness wins remain.

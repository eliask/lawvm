# LawVM Stack Map

Status: living reference.
Kind: descriptive.

Purpose: stable orientation map for the current actual architecture. Read this
first before any LawVM work. Not a target spec — describes what exists now.

## 1. Pipeline Overview

```
Amendment XML (Finlex Open Data API / Farchive)
  │
  ├── lxml parse (read-only, never mutated)
  │
  ├── PEG3 parser ─── tokenize → filter → parse ──→ ParsedOp
  │     file: finland/johtolause/peg3.py (~2200 lines)
  │     types: finland/johtolause/types.py (ParsedOp, LegalAddress)
  │     tests: tests/test_peg_curated.py (91 cases)
  │
  ├── Lowering ─── ParsedOp.to_legal_operation() ──→ LegalOperation
  │     file: core/ir.py (LegalOperation, IRNode, xml_to_ir_node)
  │
  ├── Grafter orchestration ──→ AmendmentOp + constraint predicates
  │     file: finland/grafter.py (~3000 lines) — central orchestrator
  │     constraints: finland/constraints.py (7 filter predicates)
  │     scope: finland/scope.py (chapter/scope inference)
  │     helpers: finland/helpers.py
  │
  ├── Payload extraction ─── xml_to_ir_node ──→ IRNode tree
  │     file: core/ir.py (xml_to_ir_node, positional labels)
  │
  ├── Pre-resolve ─── _pre_resolve_omissions ──→ IRNode merge
  │
  ├── Apply ─── tree_ops on replay tree ──→ mutated IRNode tree
  │     file: core/tree_ops.py (~537 lines, pure functional)
  │     ops: replace_at, remove_at, insert_sorted, check_invariants
  │     apply: finland/apply.py
  │     side output: lo_ops_out (section snapshots, post-apply)
  │
  ├── Timeline ─── compile_timelines(base, lo_ops_out)
  │     file: core/timeline.py (~911 lines)
  │
  ├── Materialization ─── materialize_pit(timelines, date, base)
  │     output: master.ir = PIT body (the canonical output)
  │     output: master.timelines = compiled timelines
  │
  └── Evidence ─── oracle comparison → adjudication → proof claims
        file: tools/evidence.py, tools/evidence_render.py
        CLI: lawvm evidence-review
```

All paths relative to `src/lawvm/`.

## 2. Key Architectural Properties

**Timeline-primary.** `replay_xml` always compiles timelines and materializes
PIT. `master.ir` IS the PIT body. The replay tree is internal machinery for
address resolution during compilation — never read from `master.tree` (lxml)
after replay.

**Fully synchronous.** No asyncio, no aiohttp, no runtime LLM calls. All
tools are sync. Run from `LawVM/` with `uv run lawvm <cmd>`.

**IRNode-native.** All mutations go through `tree_ops` on IRNode. No lxml
mutations. Positional labels ("1","2","3") assigned in `xml_to_ir_node`.

**Base-template materialization.** PIT body preserves unlabeled content
(cross-headings, liite, voimaantulo) from base body structure via overlay.

## 3. The Three Waists (Current vs Target)

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
| Surface syntax | peg3.py filters + grammar | peg3.py (tag not delete, eventually clausekit) |
| Payload shape | grafter.py payload extraction | separate payload IR (FINLAND_PAYLOAD_IR_SPEC) |
| Elaboration (stateless) | grafter.py repair/supplement | should be explicit structural elaboration pass |
| Elaboration (stateful) | grafter.py slot assignment | should use typed constraint problem |
| Replay execution | grafter.py + tree_ops + apply.py | correct location, keep boring |
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
finland/        — FI-specific parser, grafter, scope, constraints
estonia/        — EE pipeline (fetch, peg, grafter, replay)
uk/             — UK pipeline (grafter, amendment replay)
norway/, sweden/, eu/  — early-stage pipelines
tools/          — CLI commands, evidence, rendering
```

Each jurisdiction has its own parser and grafter. Core provides IRNode,
tree_ops, timeline, and LegalOperation. This is already close to the
JurisdictionPack pattern from the archived universal frontend model.

## 7. Current Accuracy

- **FI: 98.61%** (N=3545, full corpus, `LAWVM_CORPUS_STORE=transparent`)
- **EE: current replayable corpus** (N=2203 latest-version comparison cases; 343-case slice retained only as legacy release slice)
- **UK: 86.2%** (N=329 curated)

FI remaining failures: invariant-heavy mixed cases, not broad replay bugs.
2010+ PROVED_REPLAY_BUG = 0.

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

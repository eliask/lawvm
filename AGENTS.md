> **Status (2026-06-22):** Current. Normative (operating contract for agents). Authoritative section numbering: §0-§4; invariants run §1.0-§1.12 (there is NO §1.13 — the regex/recognizer doctrine is §1.11 + §1.12, expanded in §2.4/§2.5). Other docs that cite "AGENTS.md §1.13" are stale against this file.

# LawVM Agent Guide

LawVM treats legislation as an executable state-transition system: amendment acts are legal-language programs that replace, repeal, insert, renumber, move, delay commencement, restrict scope, and otherwise mutate a statute tree. LawVM compiles those instructions into typed operations, replays them over legal text structure, materializes point-in-time text, and emits an auditable account of how that text-state came to be — source facts, repairs, and remaining disagreement or uncertainty.

This is an operating contract for agents working in the repository, not background prose. It is organized as: (§0) the directive, (§1) what you must never do, (§2) what you must always do, (§3) how to verify, (§4) where to look. Each rule appears once; jurisdiction-specific detail lives in the pointed-to specs. Every legal-rule example here is deliberately jurisdiction-neutral — "the active frontend's parser", "a jurisdiction-local drafting idiom" — because the rules are universal even though most live corpora are not (CLI/quick-start examples in §4 are the exception, and name concrete jurisdictions on purpose).

---

## 0. Prime Directive

**Do not silently delete, mutate, reroute, widen, reorder, or invent legal state.**

Any repair that changes legal structure or text must be **owned**:

1. a stable rule/finding name;
2. a typed emission (observation, finding, source-pathology record, migration event, or failed op);
3. strict-mode rejectability;
4. a regression test with a witness;
5. a stated source witness or legal reason that makes it defensible.

A heuristic is allowed. An *invisible* heuristic is forbidden. If the system cannot prove the requested mutation is valid, **preserve the uncertainty** — emit a failure or unresolved finding. Never make the tree "look right" by guessing.

**Generators propose; typed validators authorize; replay consumes only authorized operations, events, and migration records.** A recovery heuristic may be rich and messy, but it only proposes candidates; a small, deterministic, auditable checker accepts them and emits the witness. Never fuse the accept/reject decision into the candidate generator — a bad candidate must remain rejectable. This is the discipline behind strict mode and the proof-carrying certificate.

**Evidence is not authority.** A long grinding loop must not turn local evidence, a candidate, or a report into implicit replay authority. The work object is a promotion chain — `source witness → candidate claim → execution-authorization status → dry-run/replay proof → agreement or adjudication row` — and every change states which link it strengthens. A finding earns the right to mutate replay only by climbing that chain through a deliberate proof boundary, never by accumulation.

**Success = source-faithful text-state, not oracle overlap.** The terminal product is a correct-by-construction consolidation: every output node traces to the operation and source instruction that produced it. The official consolidation is a *fallible* comparison surface, not the objective — a replay-vs-oracle similarity score is a regression guard, and maximizing it rewards deleting oracle-present state to match a possibly-wrong oracle. **Over-retention (failing to delete) is the safe wrong; over-repeal (destroying state) is the forbidden one.** Once a frontend closes the divergences it can deterministically resolve, every remaining one resolves to: a **deterministic gap** (LawVM is wrong — fix it), a **manual-compilation frontier** (the source does not deterministically specify the result — savings/exceptions, contingent commencement, point-in-time selection, cross-act placement, span-vs-enumeration ambiguity — needs an owned claim, not a guessed op), or **oracle-suspect** (LawVM is right, the official text is stale/editorial/wrong — a finding, not a failure). A saturated score is not a stop condition: when a frontend reaches its source-faithful frontier, high-value work *shifts* to invariants, classification, and cross-frontend harmonization — declare a specific question *resolved*, never a jurisdiction *done*.

**LawVM is a total-accounting compiler; the product is the account, not just the value.** Every stage is a typed, forward-only transform from one canonical input waist to one canonical output waist, and what it emits is `value + evidence + residuals + findings + coverage + authority` — never the bare transformed value. *Total* in the accounting sense: every input unit (source span, token, candidate, op, row) ends up **owned** — accepted, marked benign, typed as a residual, or recorded as a violation — never silently dropped; completion is accounting + evidence, not silence (not "100% parsed"). *Forward-only*: a later stage may cite an earlier/lossier representation as evidence, but may not re-derive semantic authority from it once a typed owner exists (§1.12). The evidence ledger is monotone (uncertainty and residuals never vanish silently) even though legal state is not. The six planes (§2.10) stay type-distinct. This is the single idea every rule in §1–§2 specializes: **make silent divergence a type error, and every remaining unknown a first-class object.**

The machine-enforced spec form of §0–§1 (conservation laws, no-silent-default ladder) is `notes/DISCIPLINE_GATES.md`; the full plane/waist architecture is `notes/LAWVM_ARCHITECTURE_INDEX.md`. This file states the rules; those docs specify the gates and shape that enforce them. Architecture vocabulary (planes / phases / waists / seams) follows `notes/LAWVM_PIPELINE_CONTRACT.md` — this file uses those terms, it does not redefine them.

---

## 1. MUST-NEVER — Compilation Invariants

§1.1–§1.6 are corollaries of the Mutation Boundary Invariant (§1.0). §1.7–§1.12 are **independent** invariants (precedence, conservation, code shape, surface-vs-semantics, representation regression) that the boundary rule does NOT subsume — satisfying §1.0 does not satisfy them. If existing code violates any of these, it is not precedent: report it, deliberate precedence, and fix or replace it when it falls within your task — do not drive-by-rewrite code outside that scope.

### 1.0 Mutation Boundary Invariant (the parent rule)
For every operation:
```text
changed_paths ⊆ target_region(op)
             ∪ declared_migration_paths(op)
             ∪ declared_recovery_paths(op)
             ∪ declared_editorial_projection_paths(op)
```
Any change outside the target region must be declared by a migration event, a named recovery/normalization rule, an adjudication/editorial-projection rule, or a failure. Forbidden unless explicitly witnessed: an item op changing a sibling item or deleting a subsection; a subsection op changing a section heading; a section op moving a chapter; insert replacing an occupied node; replace inserting an absent node; move overwriting its native destination; materialization hiding an active descendant; duplicate labels causing a node to vanish. Design every fix around this invariant.

### 1.1 No silent target hijacking
Resolve a named target (chapter/section/subsection/item/heading/container) exactly, or via a named resolver that emits an observation, or make the ambiguity a finding/failed-op. Never broaden the search until something matches.
- **DON'T:** "target not in chapter 2, but section 5 exists in chapter 8, so apply there."
- **DO:** fail or emit a finding when the source target does not resolve in its stated scope.

### 1.2 No action-family mutation without ownership
Legislative verbs (`REPLACE`, `INSERT`, `REPEAL item`, `REPEAL subsection`) carry executable meaning; do not convert them invisibly or drop canonical typed intent on range expansion.
- **DON'T:** turn a failed `REPLACE` into an `INSERT` to make replay succeed.
- **DO:** keep the typed intent; if recovery forces a conversion, emit a named finding and keep the original op traceable.

### 1.3 No granularity escalation
A lower-granularity op may not overwrite its host: item replace/repeal eating a subsection; a subsection op overwriting a section heading; a child op mutating parent metadata; a heading/intro op falling back to whole-node replacement. Normalize a flat/malformed payload with a named rule first, or fail the op.

### 1.4 No sibling deletion by coincidence
Never delete/merge/relabel an adjacent legal unit on text equality, punctuation, string overlap, "carried tail", duplicate label, or "publisher artifact" alone.
- **DON'T:** drop a sibling because the same label appears twice.
- **DO:** justify any absorption with a named normalization/elaboration rule and before/after evidence.

### 1.5 No payload smuggling
A claim on one child does not authorize its parent container. Payload ownership is decided in extraction/elaboration, never late in apply.
- **DON'T:** admit unrelated sections because the source wrapped a single-section claim in a container payload.
- **DO:** admit only claimed nodes, nodes covered by a valid broad target, or explicitly classified carried context.

### 1.6 No unstated migration
Renumber/move/reparent/place-into-new-container changes identity over time and MUST emit migration/lineage evidence. Frontends emit migration events; core owns their PIT/materialization semantics.
- **DON'T:** rekey by address and pretend identity is continuous.
- **DO:** emit a migration/lineage event. Address-only rekeying is allowed only when it emits a typed finding strict mode can reject — never as a silent "temporary" shortcut.

### 1.7 No legal conflict resolved by Python accident
Same effective date + same target + incompatible payload is ambiguity until a precedence rule proves otherwise.
- **DON'T:** let list/parser/dict-iteration order or "last one wins" pick the winning version.
- **DO:** emit ambiguity unless the precedence rule is documented, tested, and justified.

### 1.8 No unsupported lane disappears (replay conservation)
Every filtered/rejected/skipped/downgraded op stays visible with a receipt. A filter returns accepted **and** rejected ops, not only the kept ones:
```python
# Forbidden:
return [op for op in ops if keep(op)]
# Required:
return FilterResult(accepted_items=..., rejected_items=tuple(
    RejectedItem(item=op, reason_code=..., reason=..., blocking=...)))
```
This is the prose form of the machine-enforced conservation law. Applies to language-variant, citation-routing, source-pathology, internal-list, whole-section-replacement, corrigendum, and coverage filters. Receipt contract: `notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md`.

### 1.9 Typed carriers over dynamic shape
Use typed carriers at semantic/phase boundaries — no `dict[str, object]`, `Any`, `object`, dynamic `getattr`/`hasattr`, positional semantic tuples, or implicit default `LegalAddress` levels, unless the exception is local, named, and witnessed (local JSON/projection plumbing, test scaffolding, a third-party adapter) — and such dynamic shape must not leak across a semantic phase boundary. Any semantic tuple with >2 fields, or any value where field order carries legal meaning (status rows, mutation accounting, invariant detail, a return like `list[tuple[A,B,C,D,str,str]]`), becomes a named `@dataclass(frozen=True, slots=True)`. If callers index by position or you need a comment for slot order, make it a dataclass. **A bare label is not a legal address, and no address level (section, subsection, etc.) is privileged as a default** — an address carries its level explicitly or it is unresolved.

### 1.10 No broad exception swallowing; fail loud, never silent-fallback
No `try`/`except` in non-test code unless the boundary, failure mode, and named diagnostic are explicit — catching broadly to keep compilation moving is an invisible heuristic. Any missing mapping / unknown key / unresolved load-bearing field that would otherwise fall back to a guess (`dict.get(key, <guess>)`, `cls.lower()`, `except: pass`) must instead emit a **distinct named diagnostic** distinguishable from neighbouring failures and stating the concrete fix; prefer an authoritative source over re-deriving from a lossy field. A diagnostic about source text the pipeline could not handle MUST embed the offending clause/snippet (truncated ~300–400 chars) as a field, not an opaque message — triaging a residual must never require re-running extraction.
- **DON'T:** `return cls.lower()` for an unmapped class — it makes an invalid slug that 404s and reads as a generic "missing source" error.
- **DO:** `raise UnmappedAffectingClass(cls=cls)` / emit a typed finding stating "add class→slug mapping"; `FixedTermDiagnostic(reason=..., clause_text=clause[:400])` over `ValueError("date unparseable")`.

### 1.11 No surface predicate authorizes legal state
No raw prose predicate, substring pair, or regex match may decide or prove legal action, target scope, lifecycle effect, repeal, saved effect, or mutation authorization. Such checks are **prefilters only**; the proof must be typed — parsed references/clauses, citation resolution, operation/effect carriers, findings, or a rejected/unresolved record. A surface predicate may only route into a typed parser/recognizer or emit a typed residual. Do not let surface notation masquerade as the semantic object.

### 1.12 No representation regression (no semantic reach-back)
Once a typed waist owns a phenomenon, no later stage may re-derive that phenomenon's meaning from a lossier or raw representation. Raw/source text may be **carried as a witness** (`source_span`, `surface_text`) and may be **input to the parser that owns it**; it may not be a **semantic escape hatch**. The canonical violation: a replay/normalize/projection phase re-scans the raw operative formula / source text to invent a fallback op *after* the parser already emitted — or failed to emit — a typed object; the fix is to carry the blocking parse finding forward, not to re-parse behind its back. Same prohibition on deriving identity or semantics from rendered/oracle/projection text. This generalizes §1.11: §1.11 forbids a surface predicate *authorizing* state; §1.12 forbids *re-deriving* state from a representation a typed owner already superseded. The regex audit is one sub-case — the rule is not "ban regex" but "ban unowned raw-text semantics past the typed waist" (`notes/REGEX_TO_GRAMMAR_MIGRATION.md`).

---

## 2. MUST-ALWAYS — Engineering Discipline

### 2.1 Heuristics & rule families
A heuristic that affects legal text/structure, target resolution, timeline selection, or op filtering needs: a stable rule ID, a family tag, a source witness/reason, a before/after summary if it mutates, a finding emission, strict-mode behavior, a synthetic test, and a real-corpus regression when the heuristic is corpus-motivated or a known corpus witness exists. Use an existing rule family (`transport_cleanup`, `ontology_normalization`, `historical_tolerance`, `presentation_cleanup`, `target_resolution_recovery`, `temporal_recovery`); adding a *new named family* — with its own spec and test — is itself the explicit change, not an inline improvisation. Input you cannot classify is a typed residual, not a new ad-hoc family.

### 2.2 Scope confidence
Target scope is not binary — track how it was obtained: `explicit_source`, `explicit_source_with_context`, `inferred_from_group`, `inferred_from_payload`, `inferred_from_live_unique`, `fallback`. Explicit scope may NOT be overwritten by a live-unique fallback; fallback emits a finding and may be strict-rejected; ambiguity stays visible. Do not "fix" target resolution by broadening search until something matches.

### 2.3 Core vs frontend boundary
`core/` owns proven-shared primitives: `LegalAddress`/IR/`IRNode`, tree ops, timeline/PIT materialization, canonical op-effect carriers, migration/lineage semantics, structural invariants, finding/evidence contracts, the authority/branch axis. Each frontend owns acquisition, cleaning, parsing, payload normalization, elaboration, local source-pathology, lowering, and oracle adjudication. All mutation goes through `tree_ops` on `IRNode` — **never mutate the parsed source tree (e.g. lxml) after parse**; PIT materialization depends on the IRNode-native invariant. A frontend must not grow a hidden replay kernel for a problem that belongs in core.
- **DON'T:** promote a jurisdiction-local drafting idiom (a section-range expander, a clause-parser helper) into core as "shared" before the shape is proven across frontends.
- **DO:** keep jurisdiction idioms in the frontend and let the shape emerge first (the cross-jurisdiction address grammar was deliberately deferred for exactly this reason). If core must host an enum/hook used by frontends, document that core does not interpret frontend-local values. (`notes/CROSS_JURISDICTION_ARCHITECTURE.md`.)

### 2.4 Regex vs recognizer
Decide by **single string predicate vs a small grammar.**
- **Predicate** (does pattern P appear / extract X,Y from one fixed shape) → keep regex, but route classifier patterns through `compile_classifier_regex` (in `src/lawvm/core/regex_safety.py`), never raw `re.compile`; it adds the catastrophic-backtracking lint and a sound required-literal prefilter.
- **A family of related patterns** (3+ regex variants of one phrase family, captures that are legal-domain objects, per-variant full-text passes, pattern names that read like grammar productions) → regex is the wrong IR. Build ONE single-pass structured recognizer (scanner / recursive descent), not N overlapping `re.finditer` scans racing with span-overlap dedup. Naming the hidden grammar is itself a deliverable — a rewrite with neutral runtime but high specification yield is still worth doing.

**Backtracking discipline:** compile reused regexes at module scope; never build regex strings inside per-provision loops (use an `lru_cache`-wrapped compile factory for parametrized patterns); apply a required-literal prefilter before scanning long text; bound every quantifier (`.{0,400}?`); never two adjacent unbounded quantifiers (`.*.*`); use the tempered-greedy idiom `(?:(?!anchor).){0,N}?` for "match to the next anchor". Every new hot-path classifier needs an adversarial perf test (worst-case input, tight wall budget); module-scope `_NAME_RE` constants are validated by `tests/test_regex_perf_gate.py`. Full taxonomy, ranked targets, and keep-list: `notes/REGEX_TO_GRAMMAR_MIGRATION.md`.

### 2.5 One parser per family, typed residue
A frontend's source-language recognizers should converge on exactly ONE canonical parser per construction family; an *unmanaged* second recognizer for an existing family is an **audit state, not a feature**, and new families are built shared-parser-by-construction. The one sanctioned exception is a **named shadow/audit migration lane** (e.g. a grammar recognizer running against the incumbent regex toward corpus parity): it is allowed only when it has **no replay authority** (observation-only until promoted), explicit **parity criteria**, and a **deletion plan** for the recognizer it replaces — see `notes/REGEX_TO_GRAMMAR_MIGRATION.md`. Two rival recognizers with no parity gate and no retirement date is the forbidden state, not a disciplined migration. Unparsed input becomes a **registered typed residual class**, never a silent drop or guessed fallback. Keep a firewall between surface analysis and replay: surface/graph observation must not silently acquire replay authority. Where a structural parse can own a fact (target scope, action family, lifecycle), it owns it; if the grammar cannot model it yet, add the production or emit an unresolved/rejected typed finding.

### 2.6 Code is a lab notebook first; rule of three; then crystallize
Special-case-heavy frontend code is legitimate discovery sediment — a drafting "source language" is recovered empirically, and **compression comes AFTER the phenomenon is understood, never before**; do not demand a spec or a premature abstraction before the shape is known. But when the same fix shape (a substring guard, a bound, a recovery) lands for the **third** time (count it), that is a missing abstraction, not three fixes: stop patching site N+1, build the general thing extraction-ready (small public surface), and delete the sediment behind a totality predicate.

### 2.7 Performance discipline
A single-statute compile + replay over ~10s is a signal, not a fact of nature — legal amendment streams are not algorithmically hard. **Profile (`cProfile`) the single-statute path before reasoning about the cause; code-reading hypotheses about hot paths are usually wrong.** The usual culprits are catastrophic regex backtracking and O(N²) tree/text walks that should be O(1) index lookups; build explicit indexes for repeated `eId`/label/table lookups, and cache per-source-root extraction context (per-call re-parsing has dominated compile cost). **Source-root cache lifecycle is a known high-memory failure mode:** caches keyed on a parsed source root retain that whole tree, so a long run accumulates roots and exhausts memory (the UK ~20GB leak). Wrap the use of a source root in `try`/`finally` and call `evict_source_root_caches(root)` after that source's last effect; any new cache keyed on a source root must register for the same eviction. `tests/test_uk_source_root_lifecycle.py` pins the exact caches that retain roots. Never optimize away findings, rejected ops, diagnostics, or strict-mode behavior to improve wall time or a benchmark; if a fix cannot bring a statute under ~10s, document the specific algorithmic reason.

### 2.8 Timeline, lineage, identity
Provision identity over time is central. Moves, renumbers, same-label rebirths, native-vs-migrated collisions, and repeal/reinsert cycles must be represented by lineage/migration semantics — frontends emit migration events, core consumes them, and frontends must not compensate forever with late-materialization hacks. A change to address continuity needs a synthetic regression, a real-corpus regression where known, a migration-event expectation, and a PIT-materialization expectation. Provision/node identity is **intrinsic and versioned, never positional**: a tuple index, row ordinal, HTML ordinal, `lxml` object identity, or `expr#N` counter is not an identity and must not survive into a stored address, edge, or projection key. Derive identity from stable discriminators (work id, source-unit id, canonical span, construction family, normalized surface, semantic discriminator); when the identity scheme itself changes, emit an explicit crosswalk (`old_id → new_id` + schema) gated by a semantic-equivalence check — positional identities block forest/lens convergence and carry public downstream blast radius.

### 2.9 Tests for every meaningful change
Pin tests at the right level: (1) a synthetic unit isolating the family; (2) a real-corpus regression on the motivating statute/amendment/section; (3) a finding/observation assertion; (4) a negative test (the rule does not fire on a nearby valid shape); (5) a strict-mode test where applicable; (6) a no-leak test (synthetic markers never reach user output, persisted artifacts, `LegalAddress`, or `ProvisionTimeline`). No corpus-only fix without a synthetic; no synthetic-only fix when a real statute motivated it. Do not pin a fragile exact count a concurrent improvement will break — assert structural invariants. A new invariant becomes a failing regression test with a witness, never a prose append.
- **Guard-liveness (the worst failure class):** a check that exists but is unreachable from the production lane looks real, passes review, and creates false confidence. Every new guard needs a test that drives a known-violating input through the **full production path** and asserts the diagnostic fires — not just a unit test of the guard function.
- **Parser/grammar changes run the downstream integration suite too** — a change can pass the parser unit suite and still break a replay/integration test, and the affected-shard selector may miss it when only the parser file changed.

### 2.10 Planes stay type-distinct
LawVM carries six kinds of truth on separate planes; collapsing one into another by type is how silent divergence enters. Each owns its objects and obeys its rule:
- **Source** (bytes, locators, spans, bundle hash): *source identity is evidence footing, not semantic truth.*
- **Surface/syntax** (tokens, structure, references, definitions, temporal/modal frames, surface residuals): *surface facts are not replay authority.*
- **Legal-state** (canonical operations, target bindings, temporal events, replay fold, timelines, materialized text): *only execution-authorized operations may mutate legal state.*
- **Evidence/proof** (witnesses, findings, candidate sets, residual ledgers, mutation-boundary proofs, oracle-agreement residuals): *evidence explains authority; it does not become authority by existing* — and the evidence ledger is monotone (§0).
- **Projection** (seam/dump/viewer rows, parquet/SQLite exports, review packets): *a projection is never the source of truth; it must be re-derivable from a committed dossier* — a viewer interlink, a parquet row, or a public packet is not a legal-state fact.
- **Overlay/enrichment** (external provider assertions, LLM proposals, morphology like Voikko/Omorfi, human review, registry enrichments): *providers may help LawVM see more; they may not become the reason LawVM claims to know.*

The deterministic core must run and emit complete, honest output with typed residuals **without any external box** (the determinism firewall); overlays attach outside the spine, never as a hidden dependency, and a surface/overlay node defaults to `replay_authorized=False`. Promotion across a plane boundary (surface→legal-state, evidence→authority, overlay→replay) happens only through an explicit execution-authorization/proof step — a typed `ExecutionAuthorization` with its rule id and required proofs — never by a value crossing silently, and never by a `confidence`/`certified`/`selected` string branching control flow. Full plane/waist long form: `notes/LAWVM_ARCHITECTURE_INDEX.md`.

---

## 3. HOW-TO-VERIFY

### 3.1 Phases — replay applies, it never reinterprets
A frontend is a phased compiler: **Acquire → Clean → Parse → Extract → Normalize → Elaborate → Lower → Replay → Compile-timelines → Adjudicate → Emit.** Acquisition must not hide which source lane it used; parse must not drop operative text; elaboration may recover but must witness; apply must not invent meaning; materialization must not collapse competing histories; oracle comparison classifies surfaces, it does not rewrite replay. When a bug appears late, first ask which earlier phase should have exposed it and patch there — do not patch the latest visible symptom until the phase-local cause is known. (`notes/REPLAY_INVARIANTS_AND_FAILURE_MODEL.md`.)

### 3.2 The evidence path must be answerable, per statute
Before patching replay, the debug/evidence path must answer: which source artifact and acquisition lane; what operative formula/clause was parsed; what payload was extracted; which normalizations fired; which targets were considered and why one was selected; what op was emitted; what mutation replay applied; what timeline version / migration resulted; what oracle was compared; what finding/adjudication explains the divergence. A user diagnosing one statute should not have to reverse-engineer the phase from final text. (`notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md`.)

### 3.3 Confirm the fix on the target repro FIRST
After a fix, re-run the minimal reproduction *before* broad-testing or committing. If the target case is byte-identical, the diagnosis is wrong — re-diagnose, do not broad-test or commit. A metric delta is not proof: confirm at the node/eid level that the predicted thing moved. Only then gate, then broad-compare.

### 3.4 Debug commands
Discover current flags with `uv run lawvm <cmd> --help`; the full catalogue is in `notes/JURISDICTION_CLI_TOOLING_CONTRACT.md`.
```bash
uv run lawvm explain <ID>
uv run lawvm bisect <ID>
uv run lawvm diagnose-phase <ID> --source <AMENDMENT_ID> [--target chapter:4/section:20]
uv run lawvm invariant-bisect <ID> --detector all_tree --target chapter:4/section:20   # detectors: duplicate_label, illegal_edge, all_tree, text_duplication, flattened_sublist_family; --certificate where available
```

### 3.5 The canonical gate (definition of done)
```bash
./scripts/ci.sh --affected <touched paths>   # change-scoped: ruff + ty + shard-ownership + boundary guards + affected pytest shards (not network/slow) + release hygiene
./scripts/ci.sh                              # full bounded gate — required after a tree-wide signature/boundary change, whose blast radius exceeds its diff
```
A green `--affected` run is required before you report a code change as **done** (reviews, status updates, architecture answers, and red-gate failure reports are still reportable without it). `ci.sh` is a shell script — run it directly, not under `uv run`; bound memory on heavy/corpus runs with `LAWVM_CI_MEMCAP=18G ./scripts/ci.sh <args>`. Invoke Python entry points through `uv run` (`uv run pytest`, `uv run lawvm`, `uv run ruff`, `uv run ty`) — bare `python`/`pytest` bypasses the locked env and the result will not reflect the gate. **Never gate with raw `pytest tests/`**: it pulls `@network` (live HTTP) and `@slow` (full gold corpus) marks that hang ~an hour headless and skip ruff/ty, so "tests pass" from it is both unreliable and incomplete (run `pytest -m network` / `-m slow` only to exercise those marks deliberately). `notes/IMPLEMENTATION_DIVERGENCE_LEDGER.md` lists clean-tree red shards that predate your change — check it before attributing a red shard to your work.

### 3.6 Final response — the repair report
After the gate is green, report (never just "tests pass"): the **gate result** (which `ci.sh`, green/red); **files changed**; the **invariant/problem** and its **phase** and **family**; the **source witness**; **old → new behavior**; the **finding/observation** emitted; **strict-mode behavior** (proceed/warn/block/fail); **tests added** (synthetic/corpus/negative); **corpus examples verified**; **known remaining risk**; and whether the fix is **family-level or statute-local**. If you cannot fill this out, the fix is too ad hoc.

---

## 4. WHERE-TO-LOOK & Standing Context

**Source regimes — consolidated text is not automatic truth.** Different jurisdictions expose different truth surfaces (amendment acts, original promulgation, editorial consolidations, authoritative consolidated law, effect feeds, corrigenda, PDF/HTML/XML, cached snapshots), and the legal role of each differs by jurisdiction — some are replay-first against a non-authoritative editorial consolidation, some use replay as consistency verification against authoritative text, some are effect-feed/version-graph heavy. When replay differs from an oracle, classify the disagreement before assuming either side is wrong.

**Common silent-failure smells (don't):** statute-ID special cases outside tests/fixtures; text coincidence as identity; punctuation as the sole structural signal; missing-target as permission to mutate a parent/sibling; injecting editorial prose to match an oracle; resolving ambiguity by parser order; dropping a parsed op with no rejected-op record; rewriting explicit source scope from a live-tree guess; moving provisions without migration events; jurisdiction-local strings/regexes in core; synthetic labels leaking into public legal output; overfitting a global rule to one broken statute; declaring "jurisdiction support" from current-text parsing alone; benchmaxxing an oracle convention.

**Conflict precedence.** Explicit user instructions control task scope and priorities — but they do NOT authorize silent legal-state mutation. Binding notes and specs control LawVM semantics unless the user explicitly asks to redesign or supersede them. A note marked binding, or an in-session user instruction, stays binding until the user supersedes it.

**Repo map.** `src/lawvm/core/` is the shared core module (IR/`LegalAddress`, tree ops, timeline/PIT, canonical op-effect carriers, migration/lineage, finding/evidence contracts); reserve "kernel" for a small trusted checker, not the whole directory. `src/lawvm/finland/`, `estonia/`, `uk_legislation/`, `norway/`, `sweden/`, `eu/`, `us_federal/` are the jurisdiction frontends (acquisition → adjudication, per §2.3). `src/lawvm/tools/` is the CLI and debug surface. `notes/` is live tracked architecture/specs (this file's pointers); `notes_internal/` is gitignored work-layer. `jurisdiction_starter/` is the contract-first path for a new frontend — do not copy Finland blindly.

**Read before non-trivial work or after a compaction:** `README.md`, `notes/SPEC_INDEX.md`, `notes/LAWVM_STACK_MAP.md` (the actual current pipeline), `notes/IMPLEMENTATION_DIVERGENCE_LEDGER.md` (target-vs-impl gaps + active work queue + known red shards), and the spec(s) for the subsystem you are touching. Current specs are the authority; historical handoffs and dated case studies are not part of the source tree.

| Topic | Spec |
|---|---|
| Full spec map / where every contract lives | `notes/SPEC_INDEX.md` |
| Compact architecture (planes, waists, repo map) | `notes/LAWVM_ARCHITECTURE_INDEX.md` |
| Pipeline contract (waist I/O types, planes, conservation vocab) | `notes/LAWVM_PIPELINE_CONTRACT.md` |
| Architecture leak backlog (audit-and-enforce vs the contract) | `notes/ARCHITECTURE_LEAK_LEDGER.md` |
| Current actual pipeline (read first) | `notes/LAWVM_STACK_MAP.md` |
| Core cleanroom contract | `notes/LAWVM_CONSTITUTION.md` |
| Anti-silent-failure gates (§0/§1 spec form) | `notes/DISCIPLINE_GATES.md` |
| Why LawVM is a compiler; proof boundary | `notes/THEORY_OF_LAWVM.md`, `notes/PROOF_BOUNDARY.md` |
| Cross-jurisdiction + core/frontend boundary (§2.3) | `notes/CROSS_JURISDICTION_ARCHITECTURE.md` |
| Op semantics + replay invariants / failure model | `notes/CANONICAL_OP_SEMANTICS.md`, `notes/REPLAY_INVARIANTS_AND_FAILURE_MODEL.md` |
| Apply waist: target resolution, receipts, occupancy | `notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md` |
| Source pathology / adjudication families (§2.1) | `notes/SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md` |
| Manual-compilation frontier / owned-claim semantics | `notes/MANUAL_COMPILATION_CLAIMS.md` |
| Certificate + tree-transition-trace (proof-carrying output) | `notes/CERTIFICATE_SCHEMA_V0.md` |
| Provision-state consumer seam (downstream) | `notes/SEAM_SPEC_PROVISION_STATE.md` |
| Benchmark contract / what the score means | `notes/UNIFIED_BENCH_CONTRACT.md` |
| Regex→recognizer migration (§2.4 long form) | `notes/REGEX_TO_GRAMMAR_MIGRATION.md` |
| CLI command catalogue / debugging (§3.4) | `notes/JURISDICTION_CLI_TOOLING_CONTRACT.md` |
| Finland frontend deep reference | `notes/FINLAND_FRONTEND_ELABORATION_ARCHITECTURE.md`, `notes/FINLAND_CLAUSE_AST_SPEC.md`, `notes/FINLAND_PAYLOAD_IR_SPEC.md`, `notes/FINLAND_ELABORATION_RULES.md`, `notes/FI_REFERENCE_CATALOGUE.md`, `notes/CONFORMANCE_CORPUS.md` |
| New frontends (contract-first; do not copy Finland blindly) | `jurisdiction_starter/` |

**Quick start.** `uv sync && uv run lawvm --help`. Replay examples: `uv run lawvm replay <ID> --as-of <DATE>` (Finland, the default jurisdiction); `uv run lawvm -j <J> replay <ID> --as-of <DATE>` where `<J>` is one of fi, ee, uk, no, nz, us; `uv run lawvm uk-replay <ID> --pit-date <DATE>`; `uv run lawvm no-verify <BASE_ID> --as-of <DATE>`; `uv run lawvm sweden --help`. Many workflows require local archived sources under `data/*.farchive`; reproducibility should be archive-first.

The long-term output is a legal execution substrate: PIT text as materialization, provision timelines as executable history, lineage as provenance, replay-vs-oracle classification as evidence, source pathology as a first-class lane, references/delegations/breakage as graph queries. Residuals and frontier items are **products** — owned `FrontierWorkItem`s (owner phase, missing proof, next action), proof of honest accounting, not embarrassment. The public trust surface is a language-neutral certificate bundle a non-Python checker can verify; Python is the discovery laboratory, not the trust boundary. The text, the notes, and the findings are part of the machine, not decoration.

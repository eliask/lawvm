# FI Op Provenance Consolidation — Spec

Goal: collapse the scattered op-provenance / recovery / confidence / quirks-disposition
primitives into a single typed `OpProvenance` sum type carried by every op, plus a typed
`AcceptanceMode` keyed on that provenance, so that "silently relying on a guess" becomes a
**type-level impossibility** in strict mode. This mirrors the project's standing collapses
(`target_*` columns → one selector; bare status → enums).

This is a **migration that retires existing flags into the type** (deleting them), NOT a
parallel framework. See §7 Guards.

Status of this document: design complete. Phase 1 scope determined by the verified census
below. The bulk of the work (the `AmendmentOp` field block) is **Phase 2**, blocked behind
the concurrent `ops.py` `target_*`-retirement lane (referred to here as the `target_cols`
lane). Phase 1 that does not touch `ops.py` is described in §6.

---

## 0. Census methodology

Everything below was verified by reading the code at base `c64a9dac`, not from the prompt.
The single most consequential census finding contradicts the prompt's framing and reshapes
the plan; it is stated up front:

> **`quirks_disposition` is a pervasive CROSS-JURISDICTION CORE field, not a FI-local
> string, and it is NOT control flow.** It is declarative finding metadata: no production
> code anywhere in the repo branches on its value. The actual strict-vs-quirks decision is
> already made by the typed `StrictProfile` (`allows_*` booleans). Therefore typing
> `quirks_disposition` is (a) a large CORE + 4-jurisdiction + ~10-test-file migration, and
> (b) behaviour-neutral. It is NOT the conflict-free FI-local change the prompt assumed.

---

## 1. Exact census (file:line) of scattered sites in scope

### 1a. `AmendmentOp` provenance/recovery flags (`src/lawvm/finland/ops.py`)

Field declarations (the duplicate-of-each in `__init__` params is omitted; both live in
`ops.py` and are Phase-2 territory):

| Field | decl | LIVE replay consumer? | key read sites |
| --- | --- | --- | --- |
| `body_root_replace_fallback: bool` | ops.py:684 | LIVE | `group_ops.py:131` (heading dedup); diag: `body_coverage.py:546,564` |
| `fallback_provenance: bool` | ops.py:685 | LIVE | `compile_group_surface.py:125`; diag: `apply_op_closure_sweeps.py:267`, `body_coverage.py:546,562` |
| `sec1_body_johto_fallback: bool` | ops.py:686 | LIVE | `apply_runtime_support.py:3381` (placeholder→semantic repeal) |
| `uncovered_body_recovery: bool` | ops.py:688 | LIVE | `apply_structure_ops.py:3595` (chapter scaffold), `merge.py:2447`, `apply_subsection_ops.py:1232` |
| `extraction_provenance_tags: Tuple[str,...]` | ops.py:690 | LIVE | `frontend_compile.py:172,263`, `payload_normalize.py:2055,4490,4515`; ~28 write sites |
| `target_guessing_provenance_tags: Tuple[str,...]` | ops.py:691 | LIVE | `apply_item_ops.py:848`, `apply_payload_ops.py:432`, `apply_policy.py:448`, `apply_runtime_support.py:2567`, `compile_group_surface.py:126`, `payload_normalize.py:4801,5154`, `group_ops.py:374,426` |
| `scope_provenance_tags: Tuple[str,...]` | ops.py:692 | LIVE | `ops.py:285-348` (runtime/projection scope authority), `standalone_targets.py:178-179,219-220`; ~26 write sites |
| `scope_confidence: ScopeConfidence \| None` | ops.py:693 | LIVE | `ops.py:285-348`, `apply_op_closure_sweeps.py:189`, `standalone_targets.py:178-179` |

All eight are forwarded into `ResolvedOp.from_amendment_op` (ops.py:1376-1395). All eight
reads are real, replay-affecting branches keyed mostly on **exact tag-string membership**.

The witness-only provenance field that travels with these but carries zero semantics:
- `witness_rule_id: Optional[str]` — ops.py:702; only `apply_op_closure_sweeps.py:269,486`
  (diagnostic witness check) + `group_ops.py:456` (serialization). Folds into `OpProvenance`
  as the recognizer id, but is replay-inert.

### 1b. `quirks_disposition` — declarations and producers

**CORE (cross-jurisdiction) declarations** (this is the load-bearing surface):
- `core/diagnostic_records.py:20,37,57,88` (builder + `_require_non_empty_string` contract)
- `core/execution_authorization.py:39,57,76,93,129,169,228-229,249,365`
  (`ExecutionAuthorization` dataclass + count-aggregation in `_counts`)
- `core/evidence_contracts.py:103,141,180,218` (two dataclasses + two required-string contracts)
- `core/clause_ast.py:315,330,641`
- `core/timeline_results.py:311` (`def quirks_disposition(self) -> Literal["record"]`)
- `core/target_resolution.py:118,201`
- `core/temporal_resolution.py:81,145`
- `core/phase_replay_gate.py:61,101,184,219` (`_required_string` validation)
- `core/source_lane.py:53,109`; `core/source_acquisition.py:169`;
  `core/source_completeness.py:148`; `core/source_version_window.py:144,174`;
  `core/adjudication_evidence.py:154,244`; `core/manual_claims/native.py:293`;
  `core/frontend_phase_surface.py:78,107` (`FrontendDiagnostic`).

**Other jurisdictions** (prove this is not FI-local): `eu/ops_parser.py:88`,
`eu/pipeline.py:65`, `new_zealand/acquisition.py:150`, `uk_legislation/oracle_align.py:86`.

**FI declarations** (dataclass fields): `recovery_authorization_registry.py:30`,
`vts.py:85,121`, `chapter_seed.py:55`, `source_pathology_proof_registry.py:29`,
`process_route_rejection.py:69`, `acquisition.py:78`, `delegation.py:129`,
`future_repeal_prescan.py:46`, `johtolause/lower_clause_ast.py:97`,
`johtolause/surface_resolve.py:341`, `references/cross_refs.py:125`.

**FI producer-only literals** (set a constant, serialize, never read): all 66 `"record"`
sites enumerated by grep across `source_pathology_proof_registry.py` (28 sites),
`compile_group_scope_recovery.py`, `chapter_seed.py`, `*_proof_projector.py`,
`amendment_index.py:107`, `johtolause/api.py`, `johtolause_supplements.py:1262`,
`source_pathology.py:160,194,298`, `finlex_api.py:487`,
`legal_surface/delegation_edge_adapter.py:208`, etc.

**Distinct values across the whole field** (4 only):
`"record"` (66), `"apply"` (4: `chapter_seed.py:516,558`, `compile_group_scope_recovery.py:672,1100`),
`"suppress_duplicate_apply"` (1: `process_structural_prepare.py:169`),
`"skip_with_finding"` (1: `process_route_rejection.py:69`).

### 1c. Consumers / branchers on `quirks_disposition` — **NONE in production**

Repo-wide search for `quirks_disposition ==` / `in` / `!=`: zero production matches. The
only equality tests live in tests (`test_fi_clause_ast.py:734`, `test_eu_pipeline.py:191`,
`test_fi_vts.py:736`, `test_ee_fetch.py:224`, `test_fi_chapter_seed.py:364`,
`test_uk_oracle_align.py:73`, `test_uk_replay_adjudications.py:11572`,
`test_fi_lower_clause_ast.py:484`, `test_timeline_properties.py:1104,7011`).
The only production *use* of the value is **count aggregation** in
`execution_authorization.py:228` (`quirks_disposition_counts`).

### 1d. The real strict/quirks decision points (the actual arbiter)

`StrictProfile` (`core/compile_result.py:90`) with typed `allows_*` booleans is the live
toggle. Decision sites:
- `payload_normalize.py:2198` — `strict_profile is None or strict_profile.allows_omission_expansion`
- `compile_group_elaboration.py:674` — strict source-pathology rejection
- `apply_supplemental_recovery.py:217-218` — `allows_uncovered_body_recovery`
- `normalize.py` fallbacks gated by `allows_target_guessing`
- `he_acquisition.py:401,420,435,452,845,866,947` — `strict_disposition="abort"` (string is
  metadata; the actual abort is driven by the `strict: bool` param at `:1268`).

FI typed arbiter that already exists: `recovery_authorization_registry.py`
(`FinlandRecoveryAuthorizationRule`, with `strict_disposition`/`quirks_disposition` +
`required_proofs`/`forbidden_shortcuts`/`validator_status`). Core typed arbiter:
`execution_authorization.py` (`ExecutionAuthorization`, the `executable`/`replay_authorized`
two-flag promotion waist).

### 1e. The 3 normalize fallbacks

- `normalize.py:936` `parse_ops_fallback_heuristic(johto)` — bare emitter (body surface).
- `normalize.py:1154` `parse_ops_fallback_heuristic_with_coverage(johto, *, source_artifact_id)`
  — **delegates op production to the bare heuristic** (line 1173) and only *adds* passive
  `regex_recognition_coverage`. This is the form `normalize_and_compile_ops` calls.
- `normalize.py:1197` `parse_ops_title_fallback(title)` — title-surface emitter, repeal-only.

All three are census-pinned by `tests/test_fi_normalize_fallback_heuristic_census.py` and
RETAINED (load-bearing per whole-corpus census). They are gated by
`allows_target_guessing` upstream.

---

## 2. Partition: PROVENANCE (fold in) vs SEMANTIC (keep)

### Fold into `OpProvenance` (these are recovery/provenance, not meaning)

- `body_root_replace_fallback` → `Recovered(surface=BODY, recognizer=root_replace_fallback)`
- `fallback_provenance` → presence of a `Recovered` provenance at all (the boolean *is* the
  Parsed-vs-Recovered distinction; it must not survive as a separate flag).
- `sec1_body_johto_fallback` → `Recovered(surface=BODY, recognizer=sec1_body_johto)`
- `uncovered_body_recovery` → `Recovered(surface=BODY, recognizer=uncovered_body, …)`
- `extraction_provenance_tags` → `Recovered.recognizer_id` + coverage; the per-tag membership
  branches become matches on a typed recognizer-id enum (see §3 caveat — tag *strings* carry
  recognizer identity that today is matched literally).
- `target_guessing_provenance_tags` → `Recovered(... confidence)` with target-guessing as the
  defining property of a `Recovered` (vs `Parsed`); per-tag branches → typed recognizer-id.
- `scope_provenance_tags` + `scope_confidence` → these two are ALREADY a unified pair behind
  the `runtime_scope_confidence_for_op`/`projection_scope_confidence_for_op` authority
  (ops.py:285-348), with `scope_confidence` (typed `ScopeConfidence`) primary and the tags a
  compatibility fallback. They are a *sub-collapse already in progress*. `OpProvenance` should
  carry `scope_confidence` as the scope facet of `Recovered`; the raw `scope_provenance_tags`
  are the residual to retire LAST (they still feed `runtime_scope_confidence` when the typed
  carrier is absent).
- `witness_rule_id` → `OpProvenance` recognizer id (replay-inert).
- `quirks_disposition` (FI + core) → replaced by `AcceptanceMode` keyed on `OpProvenance`
  (see §3). Behaviour-neutral because nothing branches on it.

### 2.1 RETAIN-WITH-GUARD: the three `*_provenance_tags` bags are KEPT (not deleted)

The recovery-recognizer READS that were cleanly equivalent to a typed
`has_recognizer(prov, RecognizerId.X)` check have all been migrated (the specific
single-tag membership branches in `apply_item_ops`, `apply_payload_ops`,
`apply_policy`, `frontend_compile`, `payload_normalize`, `scope`, `group_ops`,
`merge`, `apply_subsection_dispatch`, etc. now route through `op_provenance.has_recognizer`).

What REMAINS reading the raw bags is exactly **two whole-bag witness reads** that
are DUAL-PURPOSE and therefore NOT collapsible onto the typed `OpProvenance`:

- `apply_op_closure_sweeps._has_conversion_witness` (LS-06): suppresses the
  unwitnessed-verb-conversion sweep when ANY extraction/target-guessing/scope tag
  (OR a typed `Recovered` provenance) is present.
- `apply_op_closure_sweeps._migration_backs_delta` (LS-10): backs an address-key
  delta when ANY scope/target-guessing tag (OR a witness rule id / scope
  confidence) is present.

Both accept tag strings that are **deliberately OUTSIDE the closed `RecognizerId`
namespace** — most importantly the conversion-witness tag
`semantic_collapse_move_renumber` (and its family). These witness tags are not
recovery recognizers: they record that a verb conversion / address rebind was
named (traceable), without naming a recovery recognizer an apply site branches on.
They carry no typed home in `RecognizerId` by design (the namespace is closed over
recovery recognizers only), so a whole-bag emptiness/membership read **cannot** be
rewritten as `isinstance(prov, Recovered)` / `has_recognizer(...)` without losing
them — an op carrying only such a witness tag has `provenance is None`.

Decision: **RETAIN the three bags.** They are not dead — they remain the carrier
for these conversion/migration witness tags outside the recovery namespace. The
typed `OpProvenance` is the canonical carrier for the *recovery* markers (and the
serialized column is reconstructed from it via the codec); the bags persist only
for the witness-tag dual purpose.

**Load-bearing proof (committed):** `test_fi_guard_liveness`'s
`drill_verb_conversion_unwitnessed_at_op_apply_lane` exercises the LS-06 sweep at
the PRODUCTION apply lane and asserts (a) `semantic_collapse_move_renumber` is NOT
a `RecognizerId` member, and (b) an op carrying only that witness tag has no typed
`Recovered` provenance, so the suppression rides ONLY on the whole-bag read. If a
future change either gives the witness tag a `RecognizerId` home or routes the
whole-bag read through typed provenance, that drill fails — the retention cannot
rot into a silent false "the bags are dead" claim.

### KEEP AS-IS (genuine executable semantics — verified LIVE consumers)

- `voimaantulo_repeal` — gates voimaantulo repeal recovery + VTS timeline
  (`vts.py:815,919,1015`, `uncovered_kumotaan_recovery.py:133-172`). Semantic.
- `move_clause_target_unit_kind` — gates move-clause destination scope/occupancy
  (`apply_policy.py:229,413,579,582,693`). Semantic.
- `is_temporary` — gates temporal subsection occupancy (`apply_subsection_ops.py:114,128,…`).
- `has_exact_bound_payload` — gates exact-payload subsection apply (same file). Semantic.
- `preserve_explicit_heading_facet` — gates heading-facet preservation
  (`frontend_compile.py:712`, `ops.py:2121`). Semantic.
- `post_repeal_item_shift_label` — gates item shift in repeal apply
  (`apply_item_ops.py:206-207,807`). Semantic.

Rationale for the cut line: a field is PROVENANCE iff removing the value would not change
*what mutation replay performs*, only the *audit story of how the op was derived*. Every
"KEEP" field above changes the mutation; every "fold" field is either a derivation marker or
a confidence/coverage witness.

**Caveat that constrains the migration (important):** several "provenance" tags are NOT pure
audit — apply stages branch on exact tag-string membership (e.g.
`apply_item_ops.py:848` keys on `unique_item_label_subsection_fallback`,
`payload_normalize.py:2055` keys on `jolloin_moment_renumber_supplement`). So the
`extraction_provenance_tags`/`target_guessing_provenance_tags` collapse is NOT a pure delete:
each literal tag that gates apply behaviour must become a typed recognizer-id the apply site
matches on. This is real semantic surface riding inside a "provenance" string bag, and is the
single biggest reason the bag-retirement is Phase 2+, not Phase 1.

---

## 3. Type design

New module: `src/lawvm/finland/op_provenance.py` (FI-local first; promote to core only after
the FI shape stabilizes — do NOT pre-generalize).

```python
class RecoverySurface(Enum):
    BODY = "body"          # johtolause body-text recovery
    TITLE = "title"        # title-only recovery (lowest tier by construction)
    SCOPE = "scope"        # chapter-scope resolution recovery
    PAYLOAD = "payload"    # sparse-omission / payload elaboration recovery

class ConfidenceTier(Enum):
    """Discrete recovery confidence. NO floats. Ordered worst→best.

    Mirrors the existing enum style (CiteConfidence/ScopeResolutionConfidence):
    string values, semantic docstrings, no numeric thresholds.
    """
    TITLE_ONLY = "title_only"      # title-surface guess; weakest
    HEURISTIC = "heuristic"        # bare body-text regex heuristic
    COVERAGE_BACKED = "coverage_backed"  # heuristic + intrinsic span coverage
    ANCHORED = "anchored"          # context-resolved against live structure

@dataclass(frozen=True, slots=True)
class RecognitionCoverage:
    """Intrinsic to Recovered — what the recognizer span-covered vs skipped.
    Folds the data that parse_ops_fallback_heuristic_with_coverage returns
    separately today."""
    recognized_spans: tuple[tuple[int, int], ...] = ()
    skipped_spans: tuple[tuple[int, int], ...] = ()

@dataclass(frozen=True, slots=True)
class Parsed:
    grammar_rule_id: str

@dataclass(frozen=True, slots=True)
class Recovered:
    surface: RecoverySurface
    recognizer_id: str                 # subsumes witness_rule_id + the load-bearing tags
    confidence: ConfidenceTier
    coverage: RecognitionCoverage = RecognitionCoverage()
    scope_confidence: "ScopeConfidence | None" = None  # scope facet

OpProvenance = Parsed | Recovered

class AcceptanceMode(Enum):
    STRICT = "strict"    # rejects any Recovered op
    QUIRKS = "quirks"    # records-with-finding

def admits(mode: AcceptanceMode, prov: OpProvenance) -> bool:
    """STRICT admits only Parsed; QUIRKS admits all. The type makes
    'silently relying on a guess in strict mode' impossible: a strict
    consumer that calls admits() cannot accept a Recovered op."""
    if mode is AcceptanceMode.QUIRKS:
        return True
    return isinstance(prov, Parsed)
```

### `recovery_authorization_registry` becomes the typed arbiter

Today `FinlandRecoveryAuthorizationRule` already has the right shape (kind, owner_phase,
family, `strict_disposition`, `quirks_disposition`, `required_proofs`, `forbidden_shortcuts`,
`validator_status`). The migration replaces its stringly `strict_disposition`/
`quirks_disposition` with the typed `AcceptanceMode`-derived disposition, and makes
`recovery_authorization_rule(kind)` the function that, given a `Recovered.recognizer_id`'s
owning rule, yields whether STRICT blocks it. The registry's `kind` keys (`PARSE.*`,
`ELAB.*`, `APPLY.*`, `COVERAGE.*`) become the recognizer-id namespace for `Recovered`.

### strict/quirks semantics

`AcceptanceMode` is **derived from**, not parallel to, `StrictProfile`. The mapping:
`StrictProfile` with all relevant `allows_*` False (e.g. `allows_target_guessing=False`,
`allows_uncovered_body_recovery=False`, `allows_fallback_whole_section_replace=False`) ⇒
`AcceptanceMode.STRICT`; otherwise per-recovery `QUIRKS`. Because the booleans are
per-recovery-family, the honest model is `mode_for(profile, prov) -> AcceptanceMode` keyed on
`Recovered.surface`/recognizer-family, not one global mode. This keeps the *single existing
arbiter* (`StrictProfile`) authoritative and prevents a second source of truth.

---

## 4. The 3 fallbacks become `Recovered` emitters

- `parse_ops_fallback_heuristic` → stamps each emitted op with
  `Recovered(surface=BODY, recognizer_id=<rule>, confidence=HEURISTIC)`.
- `parse_ops_fallback_heuristic_with_coverage` → **deleted**. Coverage becomes intrinsic:
  the bare heuristic emits `Recovered(..., confidence=COVERAGE_BACKED, coverage=…)` directly,
  populating `RecognitionCoverage` from the same `_extract_*_with_coverage` helpers it calls
  today. The caller (`normalize_and_compile_ops`) reads coverage off the provenance instead of
  off a separate `FallbackParseResult`. (The bare heuristic and the `_with_coverage` form
  already share one firing population — line 1173 — so this deletion is mechanical, not a
  behaviour change, once coverage rides on the op.)
- `parse_ops_title_fallback` → stamps `Recovered(surface=TITLE, recognizer_id=<rule>,
  confidence=TITLE_ONLY)`. Title is the lowest tier by construction.

Gate: `tests/test_fi_normalize_fallback_heuristic_census.py` must stay green — the census
counts the FINAL compiled ops, which are unchanged; only their provenance stamp is added.

---

## 5. Consumer wiring (provenance flow)

- Bench / default ingestion runs `QUIRKS`: keeps Recovered ops, flagged. No behaviour change
  vs today (today's default already keeps fallback ops; `quirks_disposition="record"` is the
  no-op metadata that becomes the `QUIRKS` mode marker).
- A certified-claim / strict consumer runs `STRICT` and calls `admits()` (or the
  registry-derived `mode_for`) before accepting an op, so a Recovered op is rejected at the
  type boundary rather than silently executed. This is the connection point to the planned
  serialized-status provenance-flow work: `ResolvedOp` already carries all eight flags
  (ops.py:1376-1395), so the serialized status surface gains one typed `provenance` field
  instead of eight, and the strict/quirks disposition is computed from it rather than stored
  as a free string.
- `execution_authorization.py`'s `quirks_disposition_counts` aggregation (the only production
  reader) becomes a count over the typed disposition — behaviour identical.

---

## 6. Phased migration plan + per-phase gates

Gates (every phase): replay byte-identity baseline = **59569 ok / 5 failed**
`{1938/83, 1943/662, 1991/893, 2026/174, 2026/183}` must be unchanged; bench floor
**≥ 98.01% / 99.76%**; `ty` clean; owning shards green; new tests registered in
`scripts/test_shard.py`.

### Phase 1 — type module + typed arbiter, NO `ops.py`, NO string-field migration

Conflict-free surface (the concurrent `target_cols` lane touches only the `ops.py`
`AmendmentOp` field block):
1. New `src/lawvm/finland/op_provenance.py` with `RecoverySurface`, `ConfidenceTier`,
   `RecognitionCoverage`, `Parsed`, `Recovered`, `OpProvenance`, `AcceptanceMode`, `admits`,
   `mode_for(profile, prov)`. Pure, no imports from `ops.py` (import `ScopeConfidence`
   lazily / via `TYPE_CHECKING` to avoid a cycle, or keep the scope facet untyped in P1).
2. Type the `recovery_authorization_registry` dispositions: replace
   `strict_disposition: str` / `quirks_disposition: str` with the `AcceptanceMode`-derived
   typed disposition and a `blocks_in_strict() -> bool` method, keeping the dict keys and the
   serialized `as_detail()` strings byte-identical (so no serialized-output / test churn).
3. Unit tests: `tests/test_fi_op_provenance.py` — `admits()` truth table; `mode_for` against
   `default_finland_strict_profile()`; registry `blocks_in_strict()` for each `kind`;
   a coverage-equivalence test proving `Recovered(COVERAGE_BACKED, coverage=…)` carries the
   same spans `parse_ops_fallback_heuristic_with_coverage` returns for a sample johtolause
   (locks the §4 deletion semantics before the deletion happens).
   Register in `scripts/test_shard.py`.

Phase 1 does NOT wire the new type into the op (that needs the `ops.py` field). It establishes
the type + the typed arbiter + the deletion-equivalence proof, so Phase 2 is a wiring change
under a green safety net. **This is deliberately on the edge of the "no parallel framework"
guard** (§7): it is admissible ONLY because (a) it retires the registry's stringly
dispositions in-place (a real deletion, not an addition), and (b) the rest is inert type +
tests that exist solely to de-risk the Phase-2 deletion. If review judges even this too
parallel, drop to Phase 1-min: only step 2 (retype the registry dispositions) + its test.

### Phase 2 — fold the eight flags into `OpProvenance` on `AmendmentOp` (BLOCKED on `target_cols` lane / #106)

Touches `src/lawvm/finland/ops.py` field block + `__init__` + `ResolvedOp.from_amendment_op`
(ops.py:1376-1395) → **hard conflict with the concurrent lane; do not start until it lands.**
Order within Phase 2 (each its own commit, each must hold all gates):
1. Add `provenance: OpProvenance | None` field to `AmendmentOp`/`ResolvedOp`; populate it at
   the three fallback emitters (§4) + the existing flag write sites; leave the old flags in
   place, derived-from / cross-checked against `provenance`. (Additive, safety overlap.)
2. Migrate the LIVE readers one family at a time, in dependency order, re-running the replay
   baseline after each:
   a. `scope_confidence`/`scope_provenance_tags` (already half-collapsed behind
      ops.py:285-348 — lowest risk; retire raw tags last, after the typed carrier proves
      total).
   b. `body_root_replace_fallback`, `sec1_body_johto_fallback`, `fallback_provenance`,
      `uncovered_body_recovery` (boolean flags → `Recovered` presence/recognizer).
   c. The two string-tag bags — **per load-bearing tag**, convert the apply-site literal
      membership branch (e.g. `apply_item_ops.py:848`) to a typed recognizer-id match. This is
      the riskiest step (real apply semantics inside the bag); one tag per commit.
3. Delete `parse_ops_fallback_heuristic_with_coverage` and its `FallbackParseResult` plumbing.
4. Delete the eight retired flags from `AmendmentOp` once no reader remains; update the
   fallback census test if op counts are provably unchanged.

### Phase 3 — `quirks_disposition` core retirement (CROSS-JURISDICTION; largest, lowest urgency)

Because `quirks_disposition` is behaviour-inert (no branching), retiring it is pure
type-tightening but it ripples through core + eu/nz/uk + ~10 test files. Sequence: introduce
a core `AcceptanceDisposition` enum; migrate `core/*` dataclasses one at a time keeping
serialized strings identical; update the contract validators
(`_require_non_empty_string` → enum check); migrate jurisdictions; update test literal
assertions to enum members last. Each dataclass its own commit; serialized output
byte-identical throughout. This phase is independent of Phase 2 and could be done by a
separate lane.

---

## 7. Guards (anti-slop constraints on the implementer)

1. **Migration, not parallel framework.** Every phase RETIRES an existing primitive
   (deletes a flag / a stringly field) as it lands. If a step only *adds* a type without a
   corresponding deletion on the same or next committed step, it is drift — stop.
2. **Discrete tiers only.** `ConfidenceTier` is an enum. No floats, no numeric thresholds, no
   "score". Matches `CiteConfidence`/`ScopeResolutionConfidence` style.
3. **Do not fold semantic flags.** The six KEEP fields in §2 stay. The cut line is "does the
   value change the mutation replay performs" — provenance never does.
4. **One source of truth for strict/quirks.** `AcceptanceMode` is *derived from*
   `StrictProfile`, never a second toggle. `mode_for(profile, prov)` is the only bridge.
5. **`quirks_disposition` is behaviour-inert — keep it that way.** Do not introduce branching
   on the new disposition during migration; serialized output stays byte-identical until the
   field is deleted. The whole Phase 3 is type-tightening, not a behaviour change.
6. **Load-bearing tags are semantics in disguise.** Each apply-site literal-tag branch
   (§2 caveat) must become a typed recognizer-id match, one tag per commit, replay baseline
   re-run each time. Never bulk-delete the tag bags.
7. **Respect the conflict boundary.** Phase 1 must not edit `src/lawvm/finland/ops.py`.
   Phase 2 begins only after the `target_cols` / `target_*`-retirement lane lands.

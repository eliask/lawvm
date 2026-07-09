# LawVM Discipline Gates

Status: normative. Governs the machine-enforced anti-silent-failure gates and the
shared conservation-law primitives they are built on.

LawVM's central failure mode is the **silent default**: a quantity that vanishes
untyped — a source effect that produced neither an op nor a finding, a missing
registry key that falls back to the most-optimistic value, a bug-kind downgraded
to a non-blocking observation with no recorded reason. These are review-enforced
conventions in many codebases. Here they are machine-enforced gates.

## The enforcement ladder

For each discipline, climb as high as the case allows — prefer eliminating the
trap over testing for it:

1. **Eliminate (tier 1)** — single source of truth / derive-don't-duplicate, so
   there is no "remember to update two places".
2. **Fail loud on the gap (tier 2)** — a missing registry key / unmatched case
   emits a *distinct named diagnostic* or *typed finding*; it must NOT silently
   default to the optimistic value. In hot production paths prefer a typed
   finding visible in the census; reserve a hard `raise` for test-time
   invariants and clearly-illegal states.
3. **Test for the gap (tier 3)** — a completeness/invariant gate.

## The two conservation laws

The spine is two conservation laws — "no quantity vanishes untyped". They are
twins: one on the *scoring* boundary, one on the *replay* boundary.

### Scoring side — every error is typed

`lawvm.core.bench_contract.check_residue_reconciliation`:

    For a scored unit with a structural axis:
        structural_err > 0  ⟺  Σ residue_buckets > 0.

No silent unexplained structural error (positive error, no typed residue) and no
phantom residue (typed residue, zero error). Pre-existing; the template for the
replay-side twin.

### Replay side — every source effect leaves with a receipt

`lawvm.core.replay_conservation` (this pass). For each source effect, EXACTLY
ONE of {an op was emitted, a typed rejection/finding was emitted} holds, and
every emitted op traces to a source warrant. One invariant subsumes three
silent-failure classes:

- **silent-drop**    — a source effect with no receipt at all;
- **silent-consume** — an effect marked *handled* (`OP_EMITTED`) that produced
  zero ops and no finding (the "`handled=True` returned with no ops and no
  finding" class);
- **silent-widen**   — an emitted op with no source warrant / a receipt for an
  effect the source never produced.

The Finland johtolause five-bucket census
(`lawvm.finland.johtolause.census_accounting`) is the canonical *instance* of
this law: every amendment clause lands in exactly one of
{`grammar_owned_0delta`, `legacy_fallback_registered`,
`legacy_fallback_unregistered`, `genuine_delta_unclassified`,
`genuine_delta_adjudicated_fix`}, the five buckets partition the corpus, and
`legacy_fallback_unregistered` (the closed-set breach bucket) is zero. The
jurisdiction-agnostic `PartitionCensus` primitive lifts that shape so any
frontend can certify the same partition; `test_replay_conservation` proves the
live FI census satisfies it through the shared primitive.

## Discipline classes (each gate, its violation class, tier, validation)

### A. Replay-side conservation gate — `tests/test_replay_conservation.py`

- Class: silent-drop / silent-consume / silent-widen / partition leak.
- Tier: 2 (per-receipt invariants raise eagerly) + 3 (cross-receipt partition).
- Primitive: `lawvm.core.replay_conservation` — `EffectReceipt`, `EffectLedger`,
  `PartitionCensus`.
- Witnessing example: an `EffectReceipt(disposition=OP_EMITTED, op_count=0)`
  raises `ReplayConservationError("… SILENT-CONSUME …")`; a `PartitionCensus`
  with a bucket outside the closed set is flagged as an "undeclared bucket"
  (the untyped 'other' bucket the law forbids).
- Validated: synthetic fixtures fire on each class and pass on the correct
  shape; a real-corpus spot-check (archive-gated) runs the live FI census
  through `PartitionCensus` and asserts a clean partition.

### B. Fail-loud-on-missing-key — `tests/test_jurisdiction_starter_us_federal_spec_ledger.py`

- Class: optimistic-default-on-miss (a missing registry key silently returns the
  most-confident value).
- Tier: 2 (fail loud) + a 3 completeness gate.
- Site: `lawvm.tools.spec_ledger_us_catalog.us_confidence`. Previously
  `_US_RULE_CONFIDENCE.get(rule_id, US_CONFIDENCE_CERTAIN)` — an *uncataloged*
  rule id silently became `certain` (maximum confidence). Now `certain` is the
  explicit *complement* of the heuristic set within the believed-spec catalog,
  and an uncataloged id raises `USConfidenceClassificationError` instead of
  defaulting. The adapter already routes uncataloged rules through the explicit
  `legacy_unknown` sentinel, so the live behaviour is preserved; the raise is the
  fail-loud guard against future drift.
- Witnessing example: `us_confidence("us_a_brand_new_rule_nobody_classified")`
  raises rather than returning `certain`.
- Validated: a test asserts the raise on an uncataloged id, behaviour-preserved
  on cataloged ids, every cataloged rule resolves to a known tier, and no dead
  heuristic entries.

The NZ analogue (`spec_ledger_adapter.us`-style `.get(rid, _CERTAIN if
cataloged)`) is NOT a real gap: `NZ_RULE_SPECS` and `NZ_RULE_CONFIDENCE` are both
*derived* from the single `_NZ_RULE_CATALOG`, so every cataloged rule already
carries a confidence and the `_CERTAIN` default branch is dead. That is the
tier-1 exemplar — a single source makes the optimistic default unreachable.

### C. Witness-required-for-downgrade — `tests/test_downgrade_witness.py`

- Class: a (blocking) bug-kind downgraded to a non-blocking observation with no
  recorded reason — indistinguishable from a silent suppression.
- Tier: 2 (the downgrade records a witness) + 3 (the invariant gate).
- Primitive: `lawvm.core.downgrade_witness` — a `DowngradeRecord` that downgrades
  a non-empty `bug_kind` to non-blocking must carry both a
  `reclassification_rule_id` (the witness type) and a `reclassification_reason`
  (the justification).
- Witnessing example: the Norway `sort_order` tree-invariant violation that is
  dropped from the blocking set as "spurious" (roman-numeral semantics). It
  previously vanished silently; it now records a witnessed downgrade adjudication
  (`replay_tree_invariant_violation_downgraded`) carrying the roman-recheck rule
  id and reason, so a future regression in the spurious predicate is auditable.
  The UK source-pathology nonblocking reclassification already carried both
  witness fields; the gate pins that it stays so.
- Validated: synthetic fixtures (witnessed passes, witnessless fires, missing
  either field fires); a real spot-check exercises the UK source-pathology
  downgrade path and asserts the produced record satisfies the invariant; a
  Norway test asserts the spurious `sort_order` downgrade emits a witnessed
  adjudication.

### D. AST lint — witnessless `blocking = False` downgrade — `tests/test_downgrade_witness_lint.py`

- Class: the *syntactic* form of C — a function that flips a record to
  `record["blocking"] = False` (downgrading it out of the blocking set) with no
  reclassification witness assigned in the same function.
- Tier: 3 (static gate).
- Scope is deliberately tight (near-zero false positives, per the project lint
  discipline): only literal subscript assignments of the key `"blocking"` to the
  literal `False`, requiring any one recognised witness key in the same function.
  Currently clean across `src/lawvm/` — the gate codifies the existing discipline
  and blocks a future witnessless downgrade.
- Validated: an anti-vacuity synthetic that the gate flags a witnessless
  downgrade and accepts a witnessed one.

Other lint shapes were considered and **dropped** on precision grounds: a
broad-`except`-swallow lint matches ~70 legitimate best-effort sites (it would be
`# noqa`'d into uselessness), and bare-`except:` is already enforced by ruff
(`E722`). The semantic weight sits on the conservation gate (A), not on lints.

### E. Dual-registration audit + completeness gates — `tests/test_dual_registration_completeness.py`

The "register X in two places" registries, with their tier after audit:

| Registry | Source of truth | Tier | Guard |
|----------|-----------------|------|-------|
| `FINDING_REGISTRY` | `core/observation_registry.py` | 1 | single source; `test_finding_registry.py` |
| Census buckets | `finland/johtolause/census_accounting.py` | 1 | closed tuple; `test_fi_census_accounting.py` partition test |
| NZ rule catalog | `_NZ_RULE_CATALOG` (specs+confidence derived) | 1 | single source |
| Recovery rule registry | `finland/recovery_rule_registry.py` | 1 | single tuple |
| UK bucket proofs | `uk_legislation/execution_authorization.py` | 1 | single dicts |
| US confidence split | `_US_RULE_SPECS` + `_US_RULE_CONFIDENCE` | 3 | guarded (Lane B completeness test) |
| EE / UK rule catalogs | `spec_ledger_{ee,uk}_catalog.py` | 3 | coverage + dead-entry tests |
| **Bench comparators** | `_COMPARATORS` + per-jurisdiction `register_*` | 3 | **gate added this pass** |
| **Projection detectors** | three registries + Finland `register_*` | 3 | **gate added this pass** |

The two previously *unguarded* dual registrations now have completeness gates:

- **Bench comparators** — every expected jurisdiction (`fi/us/uk/nz/ee`) must
  register a comparator; no missing, no surprise; the missing-key path fails loud
  (`KeyError`), never silently defaults.
- **Projection detectors** — the oracle-text-normalizer and
  presentation-structural-diff-detector registries must carry exactly their
  expected jurisdiction set and stay in lockstep (a jurisdiction cannot register
  one and silently omit the other).

### F. Determinism firewall — no LLM client in the replay cone — `tests/test_determinism_firewall.py`

- Class: a live, non-deterministic LLM call leaking into the byte-deterministic
  replay/projection path — the silent-default that would dissolve replay
  determinism, ratchet baselines, and byte-identical self-consistency.
- Tier: 3 (whole-graph static gate).
- Primitive: `scripts/inventory_module_roles.py:firewall_report` reuses the
  module-role import-graph builder, BFS-closes the replay/projection cone
  (`REPLAY_PROJECTION_CONE_ROOTS` — the per-jurisdiction replay engines + neutral
  projection/gate cores, NOT the monolithic `lawvm` CLI), and returns any
  offending edge where a cone module imports an LLM client
  (`lawvm.finland.llm_backends.*`, prefix-fenced against future siblings).
- Rule: LLM output may only create typed candidate proposals below an assurance
  ceiling; adjudication results enter replay ONLY as content-addressed, versioned
  records carrying the model id in provenance — never via a live call from a
  replay-cone module. Full contract: `notes/DETERMINISM_FIREWALL.md`.
- State: HOLDS today. The only `src/` LLM-client importer
  (`tools.cmd_propose_claims`, lazy `qwen_local`) is the manual-claims proposal
  tool, outside the replay cone. `FIREWALL_ALLOWLIST` is empty; any entry is
  tracked debt (route through a record), never a silent pass.
- Validated: guard-liveness synthetics drive `_is_llm_client` /
  `compute_firewall_edges` into their firing state (synthetic cone module
  importing a synthetic client surfaces as an offending edge) and assert an
  out-of-cone importer is NOT a breach; scan-integrity asserts the cone reaches
  the spine (>100 modules) so it cannot pass vacuously.
- **`--affected` blind spot:** this is a WHOLE-GRAPH ratchet. Like the
  classifier-wrap / regex / module-role / naming-hygiene ratchets, `ci.sh
  --affected` selects shards by touched path and MISSES a breach introduced
  outside the firewall's own files. Run it explicitly after any merge that
  adds/moves a module in the replay cone or under `finland.llm_backends`.

## How to add a new gate

1. Identify the silent-default. Name the quantity that would vanish untyped.
2. Climb the ladder: can the dual source be collapsed (tier 1)? If not, make the
   gap loud (tier 2) and add a completeness/invariant gate (tier 3).
3. Validate BOTH ways: the gate must FIRE on a synthetic fixture exhibiting the
   pattern and PASS on the correct behaviour. A gate that cannot catch its own
   motivating pattern is theatre.
4. Where a real corpus case is reachable, add it as a spot-check (archive-gated,
   skipping loudly when the corpus is absent — never fake data).

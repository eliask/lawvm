# Proof carrier — EV-05 / AM-01 first real measurement (EE as minting frontend)

Base commit: `745f03e6`. Scope: `core/ir.py`, `core/apply_seam.py`,
`estonia/grafter.py`, two new tests, `scripts/test_shard.py`, this note.

## What this lands (the framework change CROSS_JURISDICTION_PARITY named)

`notes/CROSS_JURISDICTION_PARITY.md` flags EV-05 and AM-01 as **not-yet-a-fix**:

> EV-05 … Closing it requires a proof carrier on `core/ir.LegalOperation` — a
> framework change, not a per-frontend gap.
> AM-01 … the seam hook exists but no sibling mints typed provenance.

The EV-05/AM-01 observe gates have been wired at the apply seam since
B-enforcement increments 1 + 4 (`notes/B_ENFORCEMENT_STATUS.md` §2, §7.1), but
every production profile inherited a NO-OP resolver: EV-05 read a ~100% firewall
hole *by construction* (no op could carry a proof), and AM-01 fired nowhere. This
task builds the **proof carrier** so both gates can finally BITE, observe-first,
with EE the first minting frontend.

### Part A — EV-05 carrier (framework)

* **`core/ir.LegalOperation`** gains an additive optional rider
  `execution_authorization: Optional[ExecutionAuthorization]` (`None` default →
  every existing op construction across all frontends stays valid and
  byte-identical; no producer sets it today). It is a typed carrier validated in
  `__post_init__` (a bare dict / string fails loud — §1.9/§1.10), mirroring the
  `scope_confidence` marker-protocol discipline. The `ExecutionAuthorization`
  import is `TYPE_CHECKING`-only with a lazy runtime import in `__post_init__`,
  because `execution_authorization` → `phase_result` → `effect_lifecycle` →
  `core.ir` is a module cycle (the `provenance.MigrationEvent` precedent).
* **`core/apply_seam.read_op_execution_authorization`** — the generic
  carrier-reading resolver (`op -> op.execution_authorization`), the framework
  counterpart to `no_op_execution_authorization`. The kernel reads the carrier; it
  never fabricates a proof (§2.10 evidence-is-not-authority).

### Part A — EE minting

* **`estonia/grafter._mint_ee_execution_authorization`** mints a typed
  `ExecutionAuthorization` from each op's GENUINELY KNOWN authority: its amending
  act (`op.source.statute_id` — the act whose johtolause directed the change).
  The proof's `authorization_rule_id` names the concrete act
  (`ee_amending_act:<statute_id>`); it is replay-authorized because for EE's
  replay lane the amending act IS the apply authority. An op with NO amending-act
  identity (`op.source` None / blank `statute_id`) has UNKNOWN authority — **no
  proof is fabricated**, so the EV-05 gate fires honestly on it.
* **`estonia/grafter._ee_execution_authorization`** (the EE
  `authorization_resolver`) prefers a proof already minted onto the op's carrier
  (the generic-resolver path) and otherwise mints one from source identity — so EE
  need not re-stamp every upstream op-construction site (byte-identity-safe).

### Part B — AM-01 resolver (EE-owned, core-neutral)

* **`estonia/grafter._ee_op_provenance_acceptance`** (the EE `provenance_resolver`)
  classifies the op as **Parsed** (admitted) or **Recovered** (refused under
  strict) from EE's OWN derivation signal — the `scope_confidence:<rung>` tag in
  `op.provenance_tags`. An *inferred/fallback* rung (`inferred_from_live_unique`,
  `inferred_from_group`, `inferred_from_payload`, `inferred_singleton_path`,
  `fallback`) = Recovered; an explicit rung / no tag = Parsed. This mirrors FI's
  reference `admits`/`mode_for` (STRICT admits only `Parsed`) WITHOUT importing
  `finland/`: the resolver hands the seam only the core-neutral `OpAcceptance`
  verdict.

Both resolvers are wired onto EE's `ApplyProfile`. **OBSERVE-only**: their
witnesses route to `AppliedOp.observations` (drained via the opt-in
`seam_observations_out`), never production `findings`. EE is **not** flipped to
block on either gate — that is a future measure-then-promote step.

## THE MEASUREMENT (the deliverable's spine)

EE's real replayable corpus (`data/estonia/current_replayable_corpus.csv` via the
production `replay_ee_to_pit` lane, archive present) was replayed in OBSERVE mode
and, per landed op, counted: EV-05 ops carrying a real `ExecutionAuthorization`
(authorized) vs not; AM-01 ops admitted (Parsed) vs not-admitted (Recovered). This
is the FIRST time either gate has a non-trivial real measurement.

| sample (statutes) | replayed ok | landed ops (denominator) | EV-05 authorized | EV-05 authorized fraction | EV-05 unauthorized (firewall hole) | AM-01 admitted (Parsed) | AM-01 not-admitted (Recovered) | AM-01 admitted fraction |
|---|---|---|---|---|---|---|---|---|
| 30  | 30  | 234 | 234 | **1.0000** | 0 | 229 | 5 | **0.9786** |
| 120 | 118 | 840 | 840 | **1.0000** | 0 | 835 | 5 | **0.9940** |

(The 2 errored statutes at the 120-sample are pre-existing replay failures —
counted as `errors`, excluded from the op denominator; the same statutes fail at
the base. The op denominator is the per-landed-op resolver-call tally, which the
seam invokes once per landed mutating op for both gates.)

### Reading the numbers honestly

* **EV-05 authorized fraction = 100%.** The measured firewall hole drops from
  ~100% (the NO-OP-resolver default) to **0% unauthorized** for EE. This is a real
  result, not a fabricated pass: the resolver returns `None` (→ the gate fires) for
  ANY op without an amending act, and EE's replayable corpus — by construction —
  contains only ops lowered from real amendment sources, so EVERY landed op has a
  known authorizing act. The gate would fire on the first corpus op whose
  authorizing act is unidentified; the corpus has none. (Synthetic-op coverage in
  `tests/test_ee_proof_carrier.py` proves the gate DOES fire on a no-source op.)
* **AM-01 admitted fraction ≈ 98–99%.** The AM-01 gate now produces a real
  measurement of EE's Recovered-vs-Parsed population: **5 Recovered ops** (the
  `scope_confidence:inferred_*` sectionless-singleton-subsection recovery family in
  `estonia/peg`) across both samples, the rest Parsed. This is the first non-zero
  AM-01 measurement anywhere outside FI — the gate fires nowhere before this land.

## Byte-identity discipline (proven)

* The carrier is additive with a `None` default → no existing op construction
  changes; the two new resolvers route exclusively to `AppliedOp.observations`,
  never to production `findings`. All 6 apply-seam byte-identity gates
  (`test_{no,se,ee,eu,uk}_apply_seam_parallel_run` + `test_us_apply_seam_boundary`)
  stay green, as do the auth / occupancy / boundary-unification / provenance /
  receipt-totality seam-gate tests + the EE occupancy/boundary enforcement tests
  and the full EE apply suite (`test_ee_apply_{conserved,semantics,filter_result}`).
* The other 5 profiles keep the kernel-default `no_op` resolvers → 0-delta.

## Test surface

* `tests/test_op_execution_authorization_carrier.py` — the EV-05 carrier
  round-trips on `LegalOperation` (default `None`; real proof survives; a bare
  dict/string fails loud at the typed waist), the generic
  `read_op_execution_authorization` resolver reads it, and wired onto a profile the
  EV-05 gate goes quiet for a carried proof / fires for an uncarried op (witness on
  `observations`, never `findings`).
* `tests/test_ee_proof_carrier.py` — EE mints a real proof naming the amending act
  / yields none for an op with no amending act / prefers a carried proof; the
  EV-05 gate is quiet for known authority and fires for unknown; the AM-01 verdict
  is admitted for Parsed and not-admitted for Recovered; `apply_ee_ops` output is
  byte-identical with the resolvers wired; the `seam_observations_out` drain
  carries a recovered witness and zero EV-05 holes (the measurement in the small).

## Staged path remaining (future measure-then-promote)

* **EV-05 block promotion (EE).** The corpus is measured 100% authorized → flipping
  EE's EV-05 gate to the strict `EVID.REPLAY_AUTHORIZATION_PROOF_REQUIRED` would be
  byte-identical on the corpus. Gated on adding an `authorization_mode` disposition
  (the `occupancy_mode` template, §7.2) + a per-profile bench review.
* **AM-01 block promotion (EE).** Not byte-identical: 5 Recovered ops would be
  refused under strict. Promotion is gated on either accepting EE's recovery
  surface in its acceptance mode or closing those 5 ops in the recognizer — a
  substantive EE step, not a profile flip.

## Pre-existing, OUT OF SCOPE (not introduced by this land)

* `tests/test_ee_structural_invariants.py` opens `open_rt_archive(readonly=True)`
  at the repo-relative default path (`<repo>/data/ee_riigiteataja.farchive`), which
  does not exist in a fresh worktree (the real archive lives at the canonical data
  root). The `open_rt_archive(readonly=True)` call is byte-identical at the base
  `745f03e6`, so these `unable to open database file` failures are an
  environment-only quirk, independent of this diff.
* Whole-repo `ty` `duckdb`/substrate unresolved-import errors and the
  `test_uk_oracle_divergence_parallel_run.py` two-shard collision in
  `scripts/test_shard.py validate` (named pre-existing in the task brief).

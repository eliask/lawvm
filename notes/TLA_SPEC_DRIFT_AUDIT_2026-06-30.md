> **Status (2026-06-30):** Current. Kind: Audit (point-in-time). Audits
> `proofs/tla/LawVMTemporalOverlay.tla` (last edited at v0.1, commit `01ab0b10`,
> never since) against the current temporal/overlay/tombstone/expiry code after
> ~88 core commits in 30 days. Primary deliverable: the per-invariant
> mirrored-by-executable-test table and the new executable mirror
> `tests/test_tla_invariant_mirror.py`. TLC was NOT run (no `tlc`/`tla2tools`
> installed; not worth a heavy toolchain — see §5).

# TLA+ Temporal-Overlay Spec Drift Audit

## 0. TL;DR

- The `.tla` model is **structurally stale but mostly still semantically
  faithful** to the modelled fragment. Two recent commits add behaviour the
  model does not express (MODEL-INCOMPLETE), and one long-standing post-v0.1
  selector rule actually **contradicts the literal `Inv_TwoRailSelection`**
  (DRIFTED-but-correct: the code is right, the spec invariant is now too strong).
- The real value isn't re-syncing prose with TLC: it's the **executable mirror**.
  Only a test that runs the real `select_active_version_ex` over a generated
  lattice stays in sync automatically. `tests/test_tla_invariant_mirror.py`
  (added by this audit) is that bridge for the selection invariants.
- Writing that mirror **immediately surfaced the drift**: the two-rail
  overlay-wins property fails on the real selector without a carve-out for the
  regime-handoff lex-posterior rule (`timeline_selection.py:470-489`). The model
  has no such rule.

## 1. Model → code map

| TLA+ construct | Current Python implementation |
|---|---|
| `timelines[a]` (per-address version seq) | `ProvisionTimeline.versions` (`core/ir.py:252`) |
| `variant = "permanent" / "temporary"` | `ProvisionVersion.variant_kind` (`core/ir.py:228`) |
| `effective` / `enacted` / `origExpires` | `ProvisionVersion.effective/enacted/expires` (`core/ir.py:226-227`) |
| `expiryChain` / `ResolvedExpiry(v)` | `OperationSource.expires_original` + `OperationSource.expiry_chain` (`ExpiryOverride`); runtime expiry = `version.expires` after replay |
| `Tomb` content / `TombstoneVersion` | repeal placeholder: selected `ProvisionVersion.content=None` + `lawvm_repeal_placeholder=1`; surfaced as `TombstoneRecord(disposition="repeal")` (`core/timeline_results.py:377`) |
| `Eligible(v,d,mode)` | `eligible(v, as_of, query_type, expires_as_of)` (`core/timeline_selection.py:192`) |
| `TempIdx` / `BgIdx` / `SelectedIdx` (two-rail) | `select_temporary_version` / `select_background_version` / `select_active_version_ex_prevalidated` (`core/timeline_selection.py:426-540`) |
| `PIT(d, mode)` / `MaterializedContent` | `materialize_pit_ex` (`core/timeline.py:1292`) |
| `MaskedByTemporaryAncestor` / `ParentNewer` / `Visible` | `_parent_content_masks_child` + overlay/superseded logic in `materialize_pit_ex` (`core/timeline.py:1521+`) |
| `OpStream` op vocab `perm/temp/commence/extend/repeal` | `IntentKind` REPLACE/INSERT/REPEAL/TEXT_PATCH/RELABEL/MOVE (`core/canonical_intent.py:266`) + action-layer relabel/renumber/split/merge/commence/temporary |
| `governing` / `in_force` modes | `query_type` (`core/timeline_selection.py:17`) |

**Bridge doc currency:** `notes/VERIFICATION_PROPERTY_MAP.md` row "Temporal
overlay semantics → TLA+ TLC (12 invariants) … Manual" is accurate that the
spec exists and is run manually, but it does NOT record that (a) the model is
frozen at v0.1, (b) there is no harness, or (c) the code has since grown
`expires_as_of`, the regime-handoff rule, and `temporary_expiry` tombstones.
The map is "present-but-stale" on this row. `notes/SEAM_SPEC_PROVISION_STATE.md`
is current (spec_version 0.3) and already documents the temporary-twin scheduler
and fixed-term expiry; it is the better bridge for the SEAM, not the kernel
invariants.

## 2. Per-invariant verdict + executable-mirror table

"Mirrored-by" = is there an executable test asserting this property **against
the real materializer/selector** (not just against a hand-built fixture echoing
the same predicate)?

| TLA+ invariant | Verdict | Mirrored against REAL code by | Notes |
|---|---|---|---|
| `Inv_TimelinesSorted` | IN-SYNC | `tests/test_timeline_properties.py::test_timeline_versions_are_monotonically_ordered` (PBT over compiled timelines) | Real compile path. |
| `Inv_NoAmbiguousPermanentPrecedence` | IN-SYNC | `check_no_overlapping_permanent_versions` (`core/timeline_invariants.py:145`) — but only unit fixtures in `test_timeline_invariants.py`; corpus-driven via `test_fi_timeline_robust_corpus.py` | NOT a generated lattice; corpus + fixtures only. |
| `Inv_TemporaryWellFormed` | IN-SYNC | `check_temporary_overlay_consistency` (`core/timeline_invariants.py:159`) + `ProvisionVersion.__post_init__` (`core/ir.py:247`); fixtures in `test_timeline_invariants.py` | Construction-time guard mirrors `expires<effective` rejection. |
| `Inv_NoOverlappingTemporaries` | IN-SYNC (model NOT contradicted by shared-sunset — see §3) | `check_temporary_overlay_consistency` overlap branch (`core/timeline_invariants.py:196-205`); fixtures only | **NOT mirrored over a generated lattice**, and the checker ignores `expires_as_of`. Frontier candidate. |
| `Inv_ExpiryChainMonotone` | IN-SYNC | `check_expiry_chain_preserved` (`core/timeline_invariants.py:215`); fixtures in `test_timeline_invariants.py` | Checks `OperationSource.expiry_chain` monotonicity. |
| `Inv_TwoRailSelection` | **DRIFTED** (code correct, spec invariant too strong) | **NEW: `tests/test_tla_invariant_mirror.py::test_inv_two_rail_selection_against_real_selector`** | Real selector adds regime-handoff lex-posterior carve-out (`timeline_selection.py:470-489`) absent from model. See §3. |
| `Inv_InForceOnlyUsesEnacted` | IN-SYNC | **NEW: `tests/test_tla_invariant_mirror.py::test_inv_in_force_only_uses_enacted_against_real_selector`** | Was previously NOT mirrored against the real selector. |
| `Inv_NoBackgroundLeakThroughActiveTempAncestor` | IN-SYNC | `check_replay_timeline_consistency` ancestor/descendant shadow checks (`core/timeline_invariants.py:445`) + `test_timeline_invariants.py` masking tests | Materialization-level; corpus + fixtures. |
| `Inv_NoOlderChildLeaksThroughNewerParent` | IN-SYNC | `_parent_content_masks_child` (`core/timeline.py:1530`) exercised by `test_timeline_properties.py` parent-replace tests | Real materialize path. |
| `Inv_NoBackgroundNoOverlayMeansAbsent` | IN-SYNC | **NEW: `tests/test_tla_invariant_mirror.py::test_no_eligible_version_means_absent_against_real_selector`** (absent direction) | Was NOT mirrored against the real selector before. |
| `Bounded_CommencedVersionsResolve` (liveness) | IN-SYNC (informal) | `test_timeline_properties.py` commencement/dormant tests | Not a generated-lattice liveness check; bounded by corpus. |
| Z3 `P1/P2/P3/P4` (selector proofs) | IN-SYNC against ABSTRACT selector only | `proofs/z3_temporal_selector.py` (abstract Z3 function, NOT real code) + **NEW** `test_selected_version_is_eligible_against_real_selector` now mirrors P1/P2 against the REAL selector | Z3 proves the abstraction; the new test proves the real function. |

**Frontier (NOT mirrored over a generated lattice against real code, only
fixtures/corpus):** `Inv_NoOverlappingTemporaries`,
`Inv_NoAmbiguousPermanentPrecedence`, `Inv_ExpiryChainMonotone`. These have
executable checkers but those checkers are themselves only fed hand-built
fixtures + the FI corpus — not a Hypothesis lattice. They are lower-risk than
the selection invariants (the two recent hot-zone commits touch selection /
tombstones, not the overlap/chain checkers), so this audit prioritised the
selection mirror.

## 3. What the two recent commits actually did (MODEL-INCOMPLETE / DRIFT detail)

### 3a. `bff7620a` "Keep oracle-reflected temporary sections alive past a shared sunset" — does NOT break `Inv_NoOverlappingTemporaries`

The concern was whether shared-sunset survival creates overlapping temporaries.
**It does not.** `_retain_oracle_reflected_section_temporary_versions`
(`finland/replay_products.py:1572`) keeps a section alive by
`dc_replace(version, expires="", variant_kind="permanent")`
(`replay_products.py:1641`) — it **promotes the retained version to a permanent
(background) version and clears `expires`**. A promoted permanent is no longer a
temporary, so it cannot overlap another temporary. `Inv_NoOverlappingTemporaries`
holds. The genuinely model-incomplete piece is the **`expires_as_of`
shared-sunset horizon** itself (`eligible(..., expires_as_of)`,
`timeline_selection.py:199`): the TLA+ `Eligible` compares `d < ResolvedExpiry(v)`
with a single date `d`, with no separate expiry horizon. **MODEL-INCOMPLETE**:
the model cannot express official-consolidation mode's split
effective-vs-expiry horizon. Low risk (the property the model states still
holds; the model just can't *describe* the official-consolidation query).

### 3b. `80d6b109` "Mint temporary_expiry tombstones" — a NEW tombstone kind the model lacks. **MODEL-INCOMPLETE.**

In the TLA+ model a tombstone is `TombstoneVersion(eff,enc) == PermVersion(eff,
enc, Tomb)` — an explicit version appended to the timeline with `content=Tomb`,
selectable like any version. The new `temporary_expiry` tombstone
(`timeline.py:1218`, `TombstoneRecord(disposition="temporary_expiry")`) is
**categorically different**: it is minted for an address where the selector
returns **None** (no eligible version, no repeal version exists) because every
version it ever had was a sunset temporary now past `expires` and never repealed.
It is a **materialization-time evidence record about an ABSENCE**, not a timeline
version. The model has no representation of "absent because all temporaries
sunset and no repeal was issued" — in the model that address simply
materializes `ABSENT` with no carrier. The model's `Tomb`/`expiryChain` cannot
express this disposition. This does not violate any safety invariant (it is
additive evidence, bench byte-identical), but it is a real expressiveness gap.

### 3c. Regime-handoff lex-posterior rule — **DRIFTED vs `Inv_TwoRailSelection`.**

`select_active_version_ex_prevalidated` (`timeline_selection.py:470-489`) sets
`overlay = None` when a permanent background's `effective` is later than the
overlay's and falls on/after the overlay's last in-force day
(`background.effective >= _day_before_iso(overlay.expires)`). Witness: 2016/258
§8 (1199/2021 commences on the same day 1458/2019's temporary text last governs;
the consolidation shows 1199's text). The TLA+ `Inv_TwoRailSelection` states
flatly that an eligible temporary overlay is ALWAYS selected (`TempIdx != 0 =>
SelectedIdx = TempIdx`). The real code does NOT obey this on the handoff day.
**The code is correct (lex posterior is right); the model invariant is now too
strong.** This was *discovered by writing the mirror test* — the naive two-rail
assertion failed on a generated example, which is exactly the value of an
executable mirror over frozen prose.

### 3d. Op vocabulary. **MODEL-INCOMPLETE (always was, not new drift).**

`OpStream` models `perm/temp/commence/extend/repeal`. The real op vocabulary is
`IntentKind` {REPLACE, INSERT, REPEAL, TEXT_PATCH, RELABEL, MOVE}
(`canonical_intent.py:266`) plus action-layer relabel/renumber/split/merge. The
model's `extend` (expiry extension) maps to `ExpiryOverride`/`expiry_chain`;
`commence` maps to deferred-enacted insert. RELABEL/MOVE/renumber (lineage
migration) are entirely outside the model — they are handled by
`migration_events` in `materialize_pit_ex`, which the model abstracts away
("body-level vs chapter-qualified alias repair … intentionally abstracted").
This is a long-standing, *intentional* abstraction boundary, not 30-day drift.

## 4. Governing vs in_force two-rail selection — IN-SYNC

`eligible()`'s `in_force` gate (`not v.enacted or v.enacted <= as_of`,
`timeline_selection.py:199`) matches `Eligible(v,d,mode)`'s
`(mode="governing" \/ v.enacted <= d)`. The new mirror test
`test_inv_in_force_only_uses_enacted_against_real_selector` confirms it against
the real selector. No drift.

## 5. TLC availability

`tlc` / `tla2tools` are NOT installed (no binary found; filesystem scan for
`tla2tools*`/`tlc*` returned nothing). Per audit guardrails this audit did NOT
install a heavy TLA+ toolchain. Even if run, **TLC checks the model against
itself, not against the code** — it would catch model-internal bugs (e.g. the
sample `OpStream` not exhibiting the claimed phenomena) but would NOT have
caught any of the §3 drifts, all of which are code-vs-model divergences. TLC-in-CI
is therefore deprioritised.

## 6. Recommended sync-lock path

**Recommendation: property-test-vs-materializer (DONE for selection) > spec
update (targeted) > TLC-in-CI (skip).**

1. **Executable mirror (highest value, DONE here).** `tests/test_tla_invariant_mirror.py`
   asserts `Inv_TwoRailSelection` (with the documented handoff carve-out),
   `Inv_InForceOnlyUsesEnacted`, the absent-direction of
   `Inv_NoBackgroundNoOverlayMeansAbsent`, and the `Eligible` gate (Z3 P1/P2
   analogue) against the REAL `select_active_version_ex` over a Hypothesis date
   lattice. It runs in CI like any pytest and moves when selector semantics move.
   **Next extension (cheap, not done):** add a generated-lattice mirror for
   `Inv_NoOverlappingTemporaries` and `Inv_ExpiryChainMonotone` feeding
   `check_temporary_overlay_consistency` / `check_expiry_chain_preserved` real
   compiled timelines instead of fixtures.

2. **Targeted `.tla` update (secondary).** Update ONLY where intended semantics
   genuinely changed:
   - **`Inv_TwoRailSelection`**: relax to admit the regime-handoff exception
     (an eligible later-effective permanent on the overlay's last in-force day
     may win), OR add a `RegimeHandoff(a,d)` predicate and weaken the invariant
     to `TempIdx != 0 /\ ~RegimeHandoff => SelectedIdx = TempIdx`. The current
     invariant is the thing that is wrong, not the code.
   - Optionally model `expires_as_of` as a second horizon and a
     `temporary_expiry`-style absence carrier — but these are expressiveness
     additions, not safety-invariant changes, and add real model complexity for
     little checking value. Lower priority.
   Do NOT chase the op-vocabulary / lineage-migration gap; it is an intentional
   abstraction boundary the model header already declares.

3. **TLC-in-CI: skip.** Weak (model-internal only); see §5.

4. **Bridge doc:** add a note to `notes/VERIFICATION_PROPERTY_MAP.md` that the
   TLA+ model is frozen at v0.1 and that the executable mirror
   (`tests/test_tla_invariant_mirror.py`) is the in-CI conformance lane for the
   selection invariants. (Not applied by this audit to avoid touching the map's
   normative rows; left as a one-line follow-up.)

## 7. Guardrail compliance

Additive only: one new test file + this note. No replay/temporal SEMANTICS
changed; no frozen seam touched; FI replay read-only. The §3c divergence is
**reported, not "fixed"** — the code is correct and the spec invariant is the
stale party.

# Proof Boundary Model

The whole domain is not just “parse some amendment prose and mutate a tree.” It is:

1. hostile source artifacts,
2. staged meaning recovery,
3. state transition,
4. temporal query,
5. external witness comparison,
6. proof/publication surfaces.

A high-assurance architecture has to police all six.

## The central theory

The assurance doctrine has five rules:

1. **No silent information loss.**
   If a stage removes or collapses structure, it must either preserve it in another typed form or emit a typed loss signal.

2. **No silent choice.**
   If more than one meaning is plausible, the compiler must either keep the ambiguity explicit or record the recovery/guess that resolved it.

3. **No impossible state.**
   Structural, temporal, or ownership contradictions are not “warnings.” They are contract breaks.

4. **No false blame.**
   Divergence from an oracle or UI artifact must not be treated as replay/source error unless the support chain proves it.

5. **No lying projections.**
   Downstream evidence, DB, cache, and UI layers must not flatten away certainty, scope, or causal support.

These rules define the proof-boundary frame.

In other words, LawVM should behave like a **proof-carrying compiler for legal deltas**:
the generator may be heuristic and messy, but every boundary needs small, explicit checkers.

---

## The right error-class taxonomy

Do not use one giant bag of “warnings.” Use six stable families.

### 1. Violation

An impossible or contract-broken state.
This is “likely bug,” “invalid IR,” or “broken invariant.”

Examples:

* tree invariant violation
* multiple active permanent versions at one address and date
* typed intent says section replace, legacy waist says heading replace
* cache artifact read under wrong schema/profile
* apply produces state that timelines cannot reproduce

Default handling:

* **hard fail**
* always CI-visible
* never downgraded to a mere observation

### 2. Ambiguity

The source permits multiple plausible meanings, or the compiler cannot prove uniqueness.

Examples:

* back-reference could bind to more than one prior target
* multiple plausible sparse-slot assignments
* anchor resolution depends on context in more than one way
* overlapping temporary overlays on the same address

Default handling:

* **strict fail**
* quirks mode may continue only if the chosen interpretation is recorded

### 3. Recovery

The compiler made a non-source-authored move to keep going.

Examples:

* scope carry-forward
* omission expansion
* uncovered-body recovery
* fallback insert/replace supplement
* replace-as-insert recovery
* family-chapter inference
* weaker duplicate target shadowed by a stronger scoped target

Default handling:

* **quirks-ok, strict-fail**
* always emitted explicitly

### 4. Source pathology

The source artifact itself is malformed, incomplete, or internally contradictory.

Examples:

* malformed XML
* duplicate structural identifiers in source
* destructive shape loss in amendment body
* missing effective date in a context that requires one
* amendment chain incomplete
* container membership mismatch rooted in source structure

Default handling:

* not a replay bug
* authoring-strict: fail
* ingestion-strict: classify separately as source-completeness/pathology

### 5. External drift / non-commensurability

The external witness is different, but the difference is not a compiler failure by itself.

Examples:

* oracle cutoff drift
* HTML/XML topology drift
* Finlex retaining expired temporary content editorially
* stale consolidated witness
* alternate editorial repeal residue

Default handling:

* **compare-time audit only**
* must not contaminate compile-time blame

### 6. Projection drift

Known facts existed upstream but were dropped, flattened, or misrepresented downstream.

Examples:

* section-level support exists but artifact only emits statute-level mixed residue
* effect intent exists but viewer discards it
* source pathology exists but publication DB loses scope
* UI card claims “Finlex wrong” without showing that it was only oracle cutoff drift

Default handling:

* CI/test failure for the projection layer
* not a source/replay error family

---

## Keep the carriers simple

The existing direction is mostly right: `PhaseResult`, `Observation`, `Obligation`, `SourcePathology`, `CompileAdjudication`, `EffectIntent`.

Refine it like this:

```python
class FindingFamily(StrEnum):
    VIOLATION = "violation"
    AMBIGUITY = "ambiguity"
    RECOVERY = "recovery"
    SOURCE_PATHOLOGY = "source_pathology"
    EXTERNAL_DRIFT = "external_drift"
    PROJECTION_DRIFT = "projection_drift"
    AUDIT = "audit"

class Enforcement(StrEnum):
    HARD_FAIL = "hard_fail"
    STRICT_FAIL = "strict_fail"
    WARN = "warn"
    INFO = "info"

@dataclass(frozen=True)
class FindingSpec:
    code: str
    phase: str
    family: FindingFamily
    default_enforcement: Enforcement
    owner: str
    description: str
```

Use these runtime carriers:

```python
@dataclass(frozen=True)
class ScopeRef:
    statute_id: str = ""
    amendment_id: str = ""
    op_id: str = ""
    address: LegalAddress | None = None
    token_span: tuple[int, int] | None = None
    rule_id: str = ""

@dataclass(frozen=True)
class Observation:
    code: str
    phase: str
    scope: ScopeRef
    detail: dict[str, Any]

@dataclass(frozen=True)
class Obligation:
    code: str
    phase: str
    scope: ScopeRef
    detail: dict[str, Any]
    blocking: bool = True

@dataclass(frozen=True)
class Violation:
    code: str
    phase: str
    scope: ScopeRef
    detail: dict[str, Any]
```

Then `PhaseResult` becomes:

```python
@dataclass(frozen=True)
class PhaseResult[T]:
    output: T
    observations: tuple[Observation, ...] = ()
    obligations: tuple[Obligation, ...] = ()
    violations: tuple[Violation, ...] = ()
```

That is enough. Do not build a giant “everything is one mega-event” object yet. The split between observation, obligation, and violation is operationally useful.

What matters is that the registry classifies each code into one of the stable families above.

---

## The boundary-contract model

Every major boundary requires a small explicit contract.

Each boundary spec should say:

* input type
* output type
* what information must be preserved
* what choices are allowed
* what invariants must hold
* what findings may be emitted
* what strict mode does with them

Like this:

```python
@dataclass(frozen=True)
class BoundarySpec:
    name: str
    input_type: str
    output_type: str
    preservation_rules: tuple[str, ...]
    allowed_recoveries: tuple[str, ...]
    invariants: tuple[str, ...]
    finding_codes: tuple[str, ...]
```

That is the right level. Not too abstract, not too implementation-bound.

---

## The actual boundary catalogue

This is the heart of it.

### 1. Source artifact boundary

Input:

* XML/JSON/archive rows/metadata

Output:

* parseable statute/amendment source bundle

Must detect:

* malformed XML
* missing chain members
* missing or contradictory dates
* duplicate IDs in source
* broken corpus packaging
* corrigendum/correction overlays

Representative codes:

* `SRC.MALFORMED_XML`
* `SRC.MISSING_AMENDMENT_CHAIN_MEMBER`
* `SRC.MISSING_EFFECTIVE_DATE`
* `SRC.DUPLICATE_STRUCTURAL_ID`
* `SRC.CORRIGENDUM_APPLIED`

Rule:

* never silently “guess corpus completeness”

### 2. Tokenization / filter boundary

Input:

* raw text

Output:

* filtered token stream with sentinels/witnesses

This is where PEG no-loss discipline lives.

Must uphold:

* no structural/backref/provenance-bearing span is silently deleted
* anything context-sensitive survives as a tagged token if not parsed yet
* every destructive filter is audited

Representative invariants:

* `SCAN.NO_STRUCTURAL_SPAN_DELETION`
* `SCAN.STRUCTURAL_TOKEN_COVERAGE`
* `SCAN.CONSECUTIVE_SPAN_SKIP_CONSUMED`

Representative codes:

* `lossy_filter_strip_risk`
* `unknown_structural_surface_form`
* `tag_not_delete_gap`
* `unconsumed_structural_residual`

This is where the “tag, don’t delete” rule belongs permanently.

### 3. Parse / clause-surface boundary

Input:

* filtered tokens

Output:

* `ClauseAST` plus `ParseWitness`

Must detect:

* partial verb groups
* exact duplicate targets
* move/renumber semantic collapse
* unresolved backrefs
* scope carry-forward dependence
* residual structural tokens after a supposedly successful parse

Representative codes:

* `duplicate_target_op`
* `semantic_collapse_move_or_renumber`
* `backref_resolution_ambiguity`
* `scope_carry_forward_required`
* `partial_verb_group`
* `unconsumed_structural_residual`

An **exact duplicate action + exact duplicate target identity** should be a first-class frontend observation.
Not a hard invariant by default, but a strict-mode failure unless the duplicates are explicitly grouped as one source effect.

### 4. ClauseAST / LegalOperation lowering boundary

Input:

* clause surface

Output:

* canonical operation IR

Must preserve:

* action family
* target family
* facet semantics
* renumber destination
* text-level substitution fields
* source witness/group identity

Representative invariants:

* `LOWER.NO_ACTION_TARGET_COLLAPSE`
* `LOWER.RENUMBER_DESTINATION_PRESERVED`
* `LOWER.FACET_SEMANTICS_PRESERVED`

Representative codes:

* `unmappable_canonical_intent`
* `invalid_unit_kind`
* `lowering_distinction_loss`
* `renumber_destination_missing`

This boundary is where “heading vs section,” “renumber vs replace,” and “move vs collapse-to-replace” must never get lost.

### 5. Frontend lowering / payload-surface boundary

Input:

* canonical ops + amendment body source

Output:

* `PayloadSurface`, jurisdictional op bundle

Must detect:

* fallback supplements
* weaker duplicates shadowed by stronger scoped targets
* scope/anchor heuristics
* body-root recovery
* unsupported source shape

Representative codes:

* `weaker_duplicate_target_shadowed`
* `chapter_scope_from_heuristic`
* `fallback_insert_supplement`
* `fallback_replace_supplement`
* `body_root_recovery_required`

This is quirks territory. Fine in quirks, explicit strict fail in strict.

### 6. Elaboration boundary

Input:

* `PayloadSurface` + typed snapshots

Output:

* elaborated intent / resolved payload bindings

This is one of the most important contracts in the whole repo.

Must uphold:

* no ambient master access
* slot assignment monotone and unique
* unresolved plurality becomes obligation, not silent choice
* destructive shape loss is observed, not normalized away
* unassigned payload leftovers survive

Representative invariants:

* `ELAB.NO_AMBIENT_MASTER_ACCESS`
* `ELAB.UNIQUE_SLOT_BINDING`
* `ELAB.NO_SILENT_PAYLOAD_DROP`

Representative codes:

* `multiple_plausible_slot_assignments`
* `unassigned_sparse_payload_slots`
* `mixed_sparse_slot_cross_paragraph_binding`
* `container_payload_pruned_shadowed_sections`
* `destructive_shape_loss_risk`
* `container_membership_mismatch`

This is where “warn early at the layer that first knows” becomes real.

### 7. Apply / replay boundary

Input:

* typed execution ops (`ResolvedOp` / `CanonicalIntent`)

Output:

* deterministic replay fold state

Must uphold:

* apply does not reinterpret
* typed intent path does not silently drop into legacy unless recorded
* occupancy contract enforced
* every mutation emits an event
* tree invariants hold after each step

Representative invariants:

* `APPLY.NO_SEMANTIC_REDISCOVERY`
* `APPLY.OCCUPANCY_ALLOWED`
* `APPLY.MUTATION_EVENT_EMITTED`
* `APPLY.TREE_INVARIANTS_HOLD`

Representative codes:

* `legacy_dispatch_fallback`
* `failed_operation`
* `occupancy_violation`
* `tree_invariant_violation`
* `typed_intent_legacy_mismatch`

This is the stage where bug-like issues should stop masquerading as mere warnings.

### 8. Timeline / PIT boundary

Input:

* replay ops or states

Output:

* provision timelines + PIT materialization

Must uphold:

* active-version selection is deterministic
* permanent vs temporary rails are explicit
* expired temporary insert means ABSENT, not editorial tombstone
* background descendants do not leak under active temporary ancestors
* known-inactive addresses do not fall back to base content
* expiry extensions preserve provenance

Representative invariants:

* `TIME.NO_OVERLAPPING_PERMANENT_VERSIONS`
* `TIME.TEMPORARY_OVERLAY_MASKS_BACKGROUND`
* `TIME.EXPIRY_CHAIN_PRESERVED`
* `PIT.KNOWN_INACTIVE_NOT_KEPT_AS_BASE`
* `TIME.REPLAY_TIMELINE_CONSISTENCY`

Representative codes:

* `active_version_collision`
* `temporary_overlay_collision`
* `expired_temp_insert_retained`
* `replay_timeline_drift`
* `same_day_precedence_implicit`

This is where the VÄLIAIKAINEN architecture should live.

### 9. Evidence / compare / oracle boundary

Input:

* replay products + witnesses/oracles

Output:

* claims, adjudications, publication-ready findings

Must uphold:

* section truth before statute summaries
* claims require support
* replay vs source vs oracle blame are separated
* editorial/Finlex conventions are not treated as legal-state truth
* section-level support survives into downstream artifacts

Representative invariants:

* `EVID.SECTION_FIRST_SUMMARY`
* `EVID.NO_CLAIM_WITHOUT_SUPPORT`
* `EVID.NO_FALSE_REPLAY_BLAME`
* `EVID.LEGAL_PIT_VS_EDITORIAL_PIT_SEPARATED`

Representative codes:

* `oracle_cutoff_drift`
* `html_xml_topology_drift`
* `source_pathology`
* `proof_gap`
* `unblamed_divergence`
* `temporary_editorial_retention`

### 10. Cache / DB / UI boundary

Input:

* typed findings and artifacts

Output:

* cached bundles, publication DB, rendered UI

Must uphold:

* cache keys include schema/profile/mode
* rendered family matches underlying fact family
* no certainty flattening
* syntax-highlight/enrichment UI is driven by structured spans, not re-regexing raw text

Representative invariants:

* `CACHE.SCHEMA_VERSION_MATCH`
* `CACHE.PROFILE_KEYED`
* `UI.RENDER_CLASS_MATCHES_FAMILY`
* `UI.NO_FACT_DROP_ON_PROJECTION`

Representative codes:

* `artifact_schema_mismatch`
* `projection_fact_drop`
* `stale_cache_family_misclass`
* `ui_flattened_certainty`

---

## The enforcement matrix

Use this everywhere:

* **Violation** → hard fail now.
* **Ambiguity** → obligation; strict fail unless profile explicitly allows.
* **Recovery** → observation + strict fail.
* **Source pathology** → observation/adjudication; not replay blame.
* **External drift** → compare-time only.
* **Projection drift** → CI/test fail in artifact layers.

And maintain three strictness lenses:

1. **authoring strict**
   Would this be acceptable as newly drafted/published law?

2. **ingestion strict**
   Can the historical corpus be compiled without heuristics?

3. **comparison strict**
   Does the external witness align with legal PIT or only with editorial convention?

That avoids conflating bad drafting, bad corpus, and bad oracle.

---

## What the ultimate VPRI-ish version looks like

Not “more clever code everywhere.”

The optimal shape is:

* large, messy generators

  * PEG
  * lowering
  * elaboration
  * uncovered-body recovery
  * evidence derivation

paired with

* **small trusted kernels**

  * no-loss checker
  * target-uniqueness checker
  * slot-assignment checker
  * tree invariant checker
  * timeline overlap/overlay checker
  * projection completeness checker

And every nontrivial step emits a witness:

* parse rule id
* source span
* slot binding
* coverage claim
* expiry override chain
* chosen anchor
* source act lineage

So the architecture is:

**generate richly, check narrowly, preserve witnesses, project honestly.**

That is the right high-assurance style for this domain.

---

## Concrete roadmap

### Phase 1: Turn the current observation registry into an assurance registry

Extend the existing registry to store:

* family
* default enforcement
* owner phase
* description

Do not add hundreds of codes yet. Start with the 30–50 already surfacing in the repo.

### Phase 2: Add `Violation` to `PhaseResult`

Keep `Observation` and `Obligation` as now.
Add `Violation`.
Stop overloading “obligation” for impossible states.

### Phase 3: Write boundary specs, not just free-text notes

Add one `BoundarySpec` per major boundary:

* scan/filter
* parse/clause
* lowering
* payload/elaboration
* apply
* timeline/PIT
* evidence/projection

These docs become the architectural center for strict mode and CI guards.

### Phase 4: Build the no-loss auditors

Highest-value immediate auditors:

* raw tokens vs filtered tokens deleted-span auditor
* ParsedOp/ClauseAST/LegalOperation lowering differential auditor
* evidence/artifact projection completeness auditor

These catch the “destroyed something too early” class, which is the most universal risk in the stack.

### Phase 5: Harden elaboration and apply contracts

Make the following first-class:

* duplicate target ops
* multiple plausible slot assignments
* target guessing required
* uncovered-body recovery required
* legacy dispatch fallback
* occupancy violations

These are the real quirks/strict frontier.

### Phase 6: Temporal kernel checkers

Add explicit validators for:

* same-address active collisions
* overlay masking
* known-inactive vs unknown-base
* expiry provenance chains
* replay fold vs timeline materialization consistency

This is where VÄLIAIKAINEN becomes principled.

### Phase 7: Evidence/projection discipline

Make it impossible for:

* section facts to disappear in statute summaries
* oracle drift to be mislabeled as replay bug
* support chains to be omitted from publication DB/cards

### Phase 8: Strict mode on top of the registry

Strict mode should not be a giant hand-curated pile of `if kind in ...`.
It should compile policy from the assurance registry:

* violations → fail
* ambiguities → fail unless allowed
* recoveries → fail unless allowed
* source pathologies → classify separately
* external drift → never compile-fail

That makes strict mode explainable and cross-jurisdiction-ready.

---

## The most useful immediate next moves

Next concrete frontier:

1. Expand `observation_registry.py` into a real assurance registry with family + enforcement metadata.
2. Add `Violation` to `PhaseResult`.
3. Introduce stable code prefixes by boundary: `SCAN.*`, `PARSE.*`, `LOWER.*`, `ELAB.*`, `APPLY.*`, `TIME.*`, `EVID.*`, `CACHE.*`.
4. Add a deleted-span PEG audit runner.
5. Add a lowering no-loss differential runner.
6. Add a projection-completeness test suite for evidence/publication artifacts.
7. Add explicit timeline overlay invariants for temporaries.

This provides a real high-assurance backbone, not just more local warnings.

The short version is:

**LawVM should treat every boundary as a proof boundary.**
Each boundary must say what it preserves, what choices it is allowed to make, what invariants must hold, and what typed facts it emits when those expectations are not met.

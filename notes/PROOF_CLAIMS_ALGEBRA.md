# Statute Rollup Claim Algebra

The main point is that statute rollup is **not** the same problem as section selection.
At section level, the hard part was “which candidate wins.”
At statute level, the hard part is “which replay-bug sections get partitioned into which rollup bucket, in what order.”

Reuse the A1 claim algebra where it fits, and add a second typed layer for the ordered section-pool partition.

## 1. Use the same logical claim types, but not the same resolver

Do not introduce a brand-new logical proof hierarchy.

Reuse these from A1:

* `ProofTier`
* `PositiveClaim`
* `UnresolvedSink`
* `RuleSpec[...]`

Do **not** make statute rollup use the section-level single-winner resolver.
Do **not** make `Defeater` central at statute level in v1.

Instead add a statute-specific wrapper and result type:

```python
from dataclasses import dataclass
from typing import Any, Mapping, TypeAlias

StatuteClaim: TypeAlias = PositiveClaim | UnresolvedSink


@dataclass(frozen=True)
class StatuteClaimRecord:
    """One emitted statute-level claim plus its presentation payload."""
    claim: StatuteClaim
    summary: str
    trigger_observations: tuple[dict[str, Any], ...] = ()

    @property
    def tier(self) -> ProofTier:
        return self.claim.tier

    @property
    def kind(self) -> str:
        return self.claim.kind

    @property
    def inference_rule(self) -> str:
        return self.claim.inference_rule

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "tier": self.claim.tier.value,
            "kind": self.claim.kind,
            "summary": self.summary,
            "inference_rule": self.claim.inference_rule,
            "trigger_observations": list(self.trigger_observations),
            "support": dict(self.claim.support),
        }


STATUTE_PRIMARY_TIER_ORDER: tuple[ProofTier, ...] = (
    ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE,
    ProofTier.PROVED_SOURCE_PATHOLOGY,
    ProofTier.PROVED_ORACLE_INCORRECT,
    ProofTier.PROVED_REPLAY_BUG,
    ProofTier.UNRESOLVED,
)


@dataclass(frozen=True)
class StatuteResolvedClaims:
    ctx: "StatuteEvidenceContext"
    claims: tuple[StatuteClaimRecord, ...]
    partition: "StatuteRollupPartition | None" = None

    @property
    def primary_tier(self) -> ProofTier:
        seen = {claim.tier for claim in self.claims}
        for tier in STATUTE_PRIMARY_TIER_ORDER:
            if tier in seen:
                return tier
        return ProofTier.UNRESOLVED

    @property
    def primary_claims(self) -> tuple[StatuteClaimRecord, ...]:
        pt = self.primary_tier
        return tuple(claim for claim in self.claims if claim.tier is pt)

    def to_legacy_claims(self) -> list[dict[str, Any]]:
        return [claim.to_legacy_dict() for claim in self.claims]
```

This provides:

* the same claim algebra as A1 at the logical layer
* statute-specific serialization fields (`summary`, `trigger_observations`)
* a multi-claim additive result, which is the real statute-level behavior

The best fit is:

**same core types, plus `StatuteResolvedClaims` and a thin statute-specific claim wrapper.**

Do not add a separate `StatuteProofClaim` hierarchy unless A1’s `PositiveClaim` is missing fields that should become universal.

## 2. `StatuteEvidenceContext`

Use a single typed context object, plus a typed bisect index and typed section rollup seeds.

### Section seed

```python
@dataclass(frozen=True)
class SectionRollupSeed:
    section: str
    diagnosis: str
    blame_source: str
    blame_title: str
    similarity: float
```

This is the typed replacement for the little `sec = {...}` dicts currently built from `section_results`.

### Lookup family

The repeated `_lookup_support_row(...)` pattern wants a type.

```python
from typing import Any, Mapping

@dataclass(frozen=True)
class RollupLookupFamily:
    exact: Mapping[str, Mapping[str, Any]]
    by_source_chapter_label: Mapping[tuple[str, str, str], Mapping[str, Any]]
    by_source_label: Mapping[tuple[str, str], Mapping[str, Any]]

    def lookup(self, seed: SectionRollupSeed) -> Mapping[str, Any] | None:
        # same semantics as _lookup_support_row(...)
        ...
```

### Bisect index

```python
@dataclass(frozen=True)
class StatuteBisectIndex:
    preexisting: RollupLookupFamily
    negligible_preexisting_drop: RollupLookupFamily
    improved: RollupLookupFamily
    repeal_only_without_payload: RollupLookupFamily
    payload_prefers_replay: RollupLookupFamily
    frontend_sparse_elaboration: RollupLookupFamily
    deterministic_sparse_oracle_stale: RollupLookupFamily
    baseline_same_chapter_drift: RollupLookupFamily
    baseline_same_section_structure_drift: RollupLookupFamily
```

### Statute context

```python
@dataclass(frozen=True)
class StatuteEvidenceContext:
    section_results: tuple[SectionRollupSeed, ...]
    stale_sections_from_diagnosis: tuple[SectionRollupSeed, ...]
    replay_bug_pool_from_diagnosis: tuple[SectionRollupSeed, ...]
    source_pathologies: tuple[dict[str, Any], ...]
    html_noncommensurable_reason: str
    missing_from_xml: tuple[str, ...]
    extra_in_xml: tuple[str, ...]
    contingent_effective_sources: tuple[str, ...]
    corrigendum_support: tuple[dict[str, Any], ...]
    oracle_suspect_detail: str
    oracle_suspect_pending: str
    section_bisect_index: StatuteBisectIndex
    alternative_replay_matches: Mapping[str, dict[str, Any]]
    oracle_range_matches: Mapping[str, dict[str, Any]]
    cross_chapter_oracle_matches: Mapping[str, dict[str, Any]]
    section_claim_results: tuple["ResolvedSectionClaims", ...] = ()
    section_claims_by_section: Mapping[str, "ResolvedSectionClaims"] = None
    selected_replay_divergence_sections: frozenset[str] = frozenset()
    selected_section_tiers: frozenset[ProofTier] = frozenset()
    selected_section_kinds: frozenset[str] = frozenset()
    has_section_results: bool = False
    all_sections_match: bool = False
```

Important derived fields:

* `replay_bug_pool_from_diagnosis` must remain the initial pool for the legacy partition logic
* `selected_replay_divergence_sections` must come from typed section claims and be used only where current code uses `section_claims`
* `selected_section_tiers` and `selected_section_kinds` support the new gap-1 unanimous rollup rule
* `all_sections_match` and `has_section_results` support the late fallback rules cleanly

That is enough to make every statute rule a pure function of one typed object.

## 3. Rule decomposition

Yes, decompose the statute rules into named functions, but **not all into the same registry shape**.

There are really two different mechanisms.

### A. Direct claim emitter rules

These are ordinary independent rules, just like A1-style emitters.

Examples:

```python
def rule_html_noncommensurable_reason(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    ...

def rule_oracle_cutoff_version_drift(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    ...

def rule_source_pathologies(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    ...

def rule_contingent_effective_sources(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    ...
```

These go in a plain `RuleSpec[StatuteClaimRecord]` registry.

### B. Ordered replay-section partition rules

Rules 7–17 are **not** cleanly independent additive emitters.
They are an ordered partition over `replay_bug_sections`.

Flattening them into ordinary additive rules would either:

* duplicate sections across buckets, or
* change the residual replay-bug set, or
* break parity

Model them as a separate rule family.

```python
@dataclass(frozen=True)
class BucketMatch:
    seed: SectionRollupSeed
    payload: Mapping[str, Any]

    def to_support_dict(self) -> dict[str, Any]:
        base = {
            "section": self.seed.section,
            "diagnosis": self.seed.diagnosis,
            "blame_source": self.seed.blame_source,
            "blame_title": self.seed.blame_title,
            "similarity": self.seed.similarity,
        }
        base.update(self.payload)
        return base


@dataclass(frozen=True)
class PartitionOutcome:
    matched: tuple[BucketMatch, ...]
    remaining: tuple[SectionRollupSeed, ...]


@dataclass(frozen=True)
class PartitionRuleSpec:
    rule_id: str
    order: int
    bucket_name: str
    apply: callable
    merge_into_stale_sections: bool = False
```

And then signatures like:

```python
def partition_preexisting_replay_sections(
    ctx: StatuteEvidenceContext,
    pool: tuple[SectionRollupSeed, ...],
) -> PartitionOutcome:
    ...

def partition_unsupported_replay_sections(
    ctx: StatuteEvidenceContext,
    pool: tuple[SectionRollupSeed, ...],
) -> PartitionOutcome:
    ...

def partition_payload_supported_replay_sections(
    ctx: StatuteEvidenceContext,
    pool: tuple[SectionRollupSeed, ...],
) -> PartitionOutcome:
    ...

def partition_deterministic_sparse_stale_sections(
    ctx: StatuteEvidenceContext,
    pool: tuple[SectionRollupSeed, ...],
) -> PartitionOutcome:
    ...

def partition_frontend_elaboration_replay_sections(
    ctx: StatuteEvidenceContext,
    pool: tuple[SectionRollupSeed, ...],
) -> PartitionOutcome:
    ...

def partition_baseline_same_chapter_drift_sections(...): ...
def partition_baseline_same_section_structure_drift_sections(...): ...
def partition_oracle_range_drift_sections(...): ...
def partition_cross_chapter_oracle_drift_sections(...): ...
def partition_same_chapter_drift_sections(...): ...
def partition_improved_replay_sections(...): ...
```

Then store the result in a typed partition trace:

```python
@dataclass(frozen=True)
class StatuteRollupPartition:
    initial_pool: tuple[SectionRollupSeed, ...]
    stale_sections: tuple[BucketMatch, ...]
    buckets: Mapping[str, tuple[BucketMatch, ...]]
    residual_replay_bug_sections: tuple[SectionRollupSeed, ...]
```

### Why special handling is necessary

Because current semantics are:

* partition first, in one order
* emit claims later, in a different order

That matters.

For example, `improved_replay_sections` is emitted before unsupported/payload buckets in the current claim list, but it is classified late in the replay-section filtering pipeline. The design therefore needs:

* a **partition registry** for classification order
* a separate **claim emitter registry** for claim append order

That is the cleanest faithful design.

## 4. Consuming typed section claims

Use typed `ResolvedSectionClaims` directly, but preserve current behavior exactly.

### What to derive from typed section claims

Add these properties to `StatuteEvidenceContext` during construction:

```python
selected_replay_divergence_sections = frozenset(
    result.ctx.section
    for result in section_claim_results
    if result.selected.tier is ProofTier.PROVED_REPLAY_BUG
    and result.selected.kind == "replay_divergence"
)

selected_section_tiers = frozenset(
    result.selected.tier
    for result in section_claim_results
)

selected_section_kinds = frozenset(
    result.selected.kind
    for result in section_claim_results
)
```

### Why not just check `tier == PROVED_REPLAY_BUG`

Because current code does **not** do that.
It only keeps residual replay sections whose selected claim is specifically:

* `tier == PROVED_REPLAY_BUG`
* `kind == "replay_divergence"`

So preserve that exact contract.

### Where typed section claims should be used

Only in the places current code already uses `section_claims`:

* final residual replay-bug rollup filter
* unanimous section-claims oracle/noncommensurable fallback
* any later intentionally added typed rollup rules

Do **not** change earlier bucket logic to depend on selected section claims unless behavior is explicitly changing.

## 5. Migration path

Yes, the same broad strategy as A1, but lighter.

### Step 1

Freeze legacy.

```python
_legacy_build_proof_claims(...)
```

### Step 2

Add typed builder.

```python
def build_proof_claims_typed(...) -> StatuteResolvedClaims:
    ...
```

### Step 3

Serialize back to legacy.

```python
typed = build_proof_claims_typed(...)
legacy_shape = typed.to_legacy_claims()
```

### Step 4

Dual-run every existing evidence test.

```python
legacy = _legacy_build_proof_claims(...)
typed = build_proof_claims_typed(...).to_legacy_claims()
assert typed == legacy
```

Because order matters, compare full list equality, not sets.

### Step 5

Add a corpus diff.

Run both builders across a representative statute corpus and diff:

* claim count
* claim order
* every field in each claim dict
* primary tier derived from claim list

### Step 6

Replace the public wrapper.

```python
def _build_proof_claims(...):
    return build_proof_claims_typed(...).to_legacy_claims()
```

### Step 7

Keep legacy for one cleanup cycle, then delete.

## 6. Is it worth doing?

My honest answer:

**yes, but not as urgently as A1, and not as a full-blown “statute-level defeater algebra.”**

Why it is worth doing:

* it removes one of the last raw-dict islands in the evidence layer
* it lets statute rollup consume typed section results cleanly instead of collapsing them back to dicts
* the ordered replay-section partition becomes explicit and testable
* it reduces the chance that section-level and statute-level logic drift apart

Why it is lower priority than A1 was:

* statute rollup is additive, so there is much less hidden control flow around winner selection
* the function is smaller
* there are no meaningful claim-vs-claim defeaters in the current behavior
* most of the complexity is in section-pool partitioning, not proof algebra per se

The right framing is:

**do a typed context + typed partition + typed claim emitter refactor, not a maximal algebra rewrite.**

That should capture most of the value.

## Concrete architecture

Split the statute rollup into four parts.

### 1. Context builder

`build_statute_context(...) -> StatuteEvidenceContext`

This builds:

* section seeds
* diagnosis-based stale/replay pools
* typed bisect indices
* typed section-claim summaries

### 2. Partition engine

`partition_replay_bug_sections(ctx) -> StatuteRollupPartition`

This applies ordered `PartitionRuleSpec`s to the replay pool.

### 3. Claim emitter

`emit_statute_claims(ctx, partition) -> tuple[StatuteClaimRecord, ...]`

This appends claims in **current legacy order**:

* direct statute facts
* oracle/stale aggregate
* bucket rollup claims
* late fallback claims

### 4. Result wrapper

`StatuteResolvedClaims`

This computes:

* ordered emitted claims
* primary tier
* legacy serialization

## Recommended registries

```python
DIRECT_STATUTE_CLAIM_RULES = (
    rule_html_noncommensurable_reason,
    rule_oracle_cutoff_version_drift,
    rule_source_pathologies,
    rule_contingent_effective_sources,
)

REPLAY_POOL_PARTITION_RULES = (
    partition_preexisting_replay_sections,
    partition_unsupported_replay_sections,
    partition_payload_supported_replay_sections,
    partition_deterministic_sparse_stale_sections,
    partition_frontend_elaboration_replay_sections,
    partition_baseline_same_chapter_drift_sections,
    partition_baseline_same_section_structure_drift_sections,
    partition_oracle_range_drift_sections,
    partition_cross_chapter_oracle_drift_sections,
    partition_same_chapter_drift_sections,
    partition_improved_replay_sections,
)

ROLLUP_CLAIM_RULES = (
    rule_oracle_support_rollup,  # uses stale_sections + missing/extra
    rule_preexisting_replay_sections_rollup,
    rule_improved_replay_sections_rollup,
    rule_unsupported_replay_sections_rollup,
    rule_payload_supported_replay_sections_rollup,
    rule_frontend_elaboration_replay_sections_rollup,
    rule_baseline_same_chapter_drift_rollup,
    rule_baseline_same_section_structure_drift_rollup,
    rule_oracle_range_drift_rollup,
    rule_cross_chapter_oracle_drift_rollup,
    rule_same_chapter_drift_rollup,
    rule_residual_replay_bug_rollup,
)

LATE_FALLBACK_RULES = (
    rule_unanimous_section_claims_oracle_or_noncomm,
    rule_all_sections_match,
    rule_no_section_results,
)

FINAL_FALLBACK_RULE = rule_no_strong_claim
```

That is the cleanest parity-preserving shape.

So the short conclusion is:

* reuse the A1 logical claim types
* add a statute-specific claim wrapper and result object
* do **not** force statute rollup into section-style winner selection
* model the replay-bug section filtering as an explicit ordered partition pipeline
* migrate the same way as A1, but treat this as a medium-priority cleanup, not a core architectural blocker

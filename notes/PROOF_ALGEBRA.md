# Proof Claim Algebra

The core migration constraint is:

**do not replace the current cascade with “fire every rule and sort” in one jump.**
That would change candidate lists and break the bit-identical constraint.
Instead, introduce a **typed algebra plus an explicit staged resolver** that mirrors today’s short-circuit structure, while making every rule independently testable.

## 1. Type definitions

The design uses five core types:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping, TypeAlias

SupportPayload: TypeAlias = Mapping[str, Any]


class ProofTier(StrEnum):
    PROVED_ORACLE_INCORRECT = "PROVED_ORACLE_INCORRECT"
    PROVED_SOURCE_PATHOLOGY = "PROVED_SOURCE_PATHOLOGY"
    PROVED_HTML_XML_NONCOMMENSURABLE = "PROVED_HTML_XML_NONCOMMENSURABLE"
    UNRESOLVED = "UNRESOLVED"
    PROVED_REPLAY_BUG = "PROVED_REPLAY_BUG"


SECTION_TIER_PRIORITY: dict[ProofTier, int] = {
    ProofTier.PROVED_ORACLE_INCORRECT: 0,
    ProofTier.PROVED_SOURCE_PATHOLOGY: 1,
    ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE: 2,
    ProofTier.UNRESOLVED: 3,
    ProofTier.PROVED_REPLAY_BUG: 4,
}


class RulePhase(StrEnum):
    PREEMPTIVE = "preemptive"
    PRIMARY = "primary"
    FALLBACK_DEFEATER = "fallback_defeater"
    PROMOTION = "promotion"
    FINAL_FALLBACK = "final_fallback"


@dataclass(frozen=True)
class ClaimSelector:
    """Typed matcher for defeaters.

    Tags are the main mechanism.
    kind/rule_id/tier are compatibility shims for narrow cases.
    """
    tags: frozenset[str] = frozenset()
    kinds: frozenset[str] = frozenset()
    rule_ids: frozenset[str] = frozenset()
    tiers: frozenset[ProofTier] = frozenset()

    def matches(self, claim: "PositiveClaim") -> bool:
        if self.tags and not (self.tags & claim.proof_tags):
            return False
        if self.kinds and claim.kind not in self.kinds:
            return False
        if self.rule_ids and claim.rule_id not in self.rule_ids:
            return False
        if self.tiers and claim.tier not in self.tiers:
            return False
        return True


@dataclass(frozen=True)
class PositiveClaim:
    """A selectable proof claim."""
    rule_id: str
    tier: ProofTier
    kind: str
    inference_rule: str
    observation_sources: tuple[str, ...]
    support: SupportPayload
    proof_tags: frozenset[str] = frozenset()

    def to_candidate_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "kind": self.kind,
            "inference_rule": self.inference_rule,
            "observation_sources": list(self.observation_sources),
            "support": dict(self.support),
        }


@dataclass(frozen=True)
class UnresolvedSink:
    """A selectable unresolved outcome.

    This is not a positive proof. It is the typed explanation for why
    no stronger positive claim should win.
    """
    rule_id: str
    kind: str
    inference_rule: str
    observation_sources: tuple[str, ...]
    support: SupportPayload

    @property
    def tier(self) -> ProofTier:
        return ProofTier.UNRESOLVED

    def to_candidate_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "kind": self.kind,
            "inference_rule": self.inference_rule,
            "observation_sources": list(self.observation_sources),
            "support": dict(self.support),
        }


class DefeaterEffect(StrEnum):
    SUPPRESS = "suppress"
    REPLACE_WITH_SINK = "replace_with_sink"


@dataclass(frozen=True)
class Defeater:
    """A typed blocker.

    In v1, use it mainly against the latent replay fallback claim.
    """
    rule_id: str
    targets: ClaimSelector
    effect: DefeaterEffect
    inference_rule: str
    observation_sources: tuple[str, ...]
    support: SupportPayload
    replacement_sink: UnresolvedSink | None = None
```

And then two wrappers for rule registration and resolution results:

```python
SelectableCandidate = PositiveClaim | UnresolvedSink


@dataclass(frozen=True)
class RuleSpec[T]:
    rule_id: str
    phase: RulePhase
    order: int
    emit: Callable[["SectionEvidenceContext"], tuple[T, ...]]


@dataclass(frozen=True)
class SuppressedCandidate:
    claim: PositiveClaim
    defeated_by_rule_id: str
    defeated_by_inference_rule: str
    replacement_sink_kind: str | None = None


@dataclass(frozen=True)
class ResolvedSectionClaims:
    ctx: "SectionEvidenceContext"
    selected: SelectableCandidate
    candidates: tuple[SelectableCandidate, ...]
    defeated_candidates: tuple[SelectableCandidate, ...]
    suppressed_candidates: tuple[SuppressedCandidate, ...] = ()
    applied_defeaters: tuple[Defeater, ...] = ()

    def to_legacy_row(self) -> dict[str, Any]:
        candidate_dicts = [c.to_candidate_dict() for c in self.candidates]
        winner = candidate_dicts[0]
        return {
            "section": self.ctx.section,
            "diagnosis": self.ctx.diagnosis,
            "blame_source": self.ctx.blame_source,
            "similarity": self.ctx.similarity,
            "strict_payload_confidence": self.ctx.strict_payload_confidence,
            "selected_kind": winner["kind"],
            "selected_tier": winner["tier"],
            "selected_inference_rule": winner["inference_rule"],
            "oracle_range_match": self.ctx.oracle_range_match or None,
            "cross_chapter_oracle_match": self.ctx.cross_chapter_oracle_match or None,
            "alternative_replay_match": self.ctx.alternative_match or None,
            "candidate_count": len(candidate_dicts),
            "candidate_kinds": [c["kind"] for c in candidate_dicts if c["kind"]],
            "defeated_candidate_kinds": [
                c["kind"] for c in candidate_dicts[1:] if c["kind"]
            ],
            "defeated_candidates": [
                {
                    "kind": c["kind"],
                    "tier": c["tier"],
                    "inference_rule": c["inference_rule"],
                    "defeated_by_kind": winner["kind"],
                    "defeated_by_inference_rule": winner["inference_rule"],
                    "defeated_by_observation_sources": list(winner["observation_sources"]),
                }
                for c in candidate_dicts[1:]
                if c["kind"]
            ],
            "candidates": candidate_dicts,
        }
```

### Relation to today’s dict candidates

`PositiveClaim` and `UnresolvedSink` are the typed version of the current candidate dict.
`Defeater` has **no current public equivalent**. It is internal structure that explains why some candidate never became selectable.

That is the most important distinction:

* **selectable**: positive claims and unresolved sinks
* **non-selectable**: defeaters

## 2. Rule registry

Use **flat tuples inside explicit phases**, not one flat rule list.

That preserves current behavior and keeps rules isolated.

### Rule layout

Split the current file into:

* `evidence_claim_algebra.py` — types, resolver, serializer
* `evidence_section_rules.py` — all rule functions and registries
* `evidence_claims.py` — compatibility façade

### Registry phases

```python
PREEMPTIVE_POSITIVE_RULES: tuple[RuleSpec[PositiveClaim], ...]
PRIMARY_POSITIVE_RULES: tuple[RuleSpec[PositiveClaim], ...]
PRIMARY_SINK_RULES: tuple[RuleSpec[UnresolvedSink], ...]
FALLBACK_DEFEATER_RULES: tuple[RuleSpec[Defeater], ...]
PROMOTION_POSITIVE_RULES: tuple[RuleSpec[PositiveClaim], ...]
FINAL_FALLBACK_RULES: tuple[RuleSpec[PositiveClaim], ...]
```

### Why phases instead of dependencies

Do **not** add rule-to-rule dependency declarations in A1.

A rule should depend only on `SectionEvidenceContext`.
If some current branch depends on “whether another branch would have emitted,” move that fact into the context.

Example: today’s `no blame + no timeline` branch checks whether a preexisting baseline-residue candidate already exists. In the new design that should become a context boolean like:

```python
ctx.has_preexisting_residue_support
```

That keeps rule functions independently testable.

### Recommended rule inventory

Keep the human 28-rule inventory, but implement it as **family-pure emitter functions**. One mixed rule becomes two functions in code where necessary.

#### Preemptive positive rules

```python
rule_oracle_stale_diagnosis
rule_oracle_temporal_impossibility
```

#### Primary positive rules

```python
rule_extra_empty_oracle_explicit_content_absent
rule_duplicate_unscoped_oracle_labels_noncommensurable
rule_same_chapter_oracle_range_drift
rule_cross_chapter_oracle_match_exact
rule_preexisting_baseline_high_confidence
rule_negligible_blame_drop_high_confidence
rule_blame_only_repeal_without_payload
rule_blame_payload_prefers_replay
rule_deterministic_sparse_oracle_stale
rule_same_chapter_alternative_match_exact
rule_no_blame_no_timeline
```

#### Primary sink rules

```python
rule_extra_empty_oracle_unverified_absence
rule_cross_chapter_oracle_match_unresolved
rule_preexisting_baseline_low_confidence
rule_negligible_blame_drop_low_confidence
rule_baseline_same_section_structure_drift
rule_blame_frontend_sparse_elaboration
rule_first_drop_frontend_sparse_elaboration
rule_blame_source_improved_or_equal
rule_baseline_alternative_match
rule_same_chapter_alternative_match_unresolved
rule_no_blame_has_timeline
```

#### Fallback defeater rules

```python
rule_extraction_gap_defeater
rule_section_source_barrier_defeater
rule_section_recovery_barrier_defeater
```

#### Promotion positive rules

```python
rule_timeline_invariant_violation
```

#### Final fallback rules

```python
rule_replay_divergence_fallback
```

### Example signatures

```python
def rule_oracle_stale_diagnosis(ctx: SectionEvidenceContext) -> tuple[PositiveClaim, ...]:
    ...

def rule_blame_frontend_sparse_elaboration(ctx: SectionEvidenceContext) -> tuple[UnresolvedSink, ...]:
    ...

def rule_extraction_gap_defeater(ctx: SectionEvidenceContext) -> tuple[Defeater, ...]:
    ...
```

### Example defeater

```python
def rule_extraction_gap_defeater(ctx: SectionEvidenceContext) -> tuple[Defeater, ...]:
    if not ctx.has_extraction_gap:
        return ()

    sink = UnresolvedSink(
        rule_id="SEC.SINK.EXTRACTION_GAP",
        kind="UNRESOLVED.source_underdetermined.extraction_coverage_gap",
        inference_rule="statute_has_extraction_fallback_so_replay_divergence_cannot_be_attributed_to_replay_logic",
        observation_sources=("oracle_check", "compile_result"),
        support={
            "diagnosis": ctx.diagnosis,
            "similarity": ctx.similarity,
            "extraction_fallback": True,
        },
    )

    return (
        Defeater(
            rule_id="SEC.DEF.EXTRACTION_GAP",
            targets=ClaimSelector(tags=frozenset({"fallback_replay_attribution"})),
            effect=DefeaterEffect.REPLACE_WITH_SINK,
            inference_rule="extraction_gap_defeats_fallback_replay_attribution",
            observation_sources=("compile_result",),
            support={"strict_fail_reasons": list(ctx.strict_fail_reasons)},
            replacement_sink=sink,
        ),
    )
```

### How defeaters should target claims

**Not by tier alone.**
Use **proof tags first**, with optional `kind`/`rule_id` selectors for compatibility.

For A1, the important tag is on the fallback replay claim:

```python
proof_tags=frozenset({
    "fallback_replay_attribution",
    "requires_complete_extraction",
    "requires_section_strict_lineage",
})
```

That lets extraction-gap and strict-lineage defeaters block only the fallback replay attribution, which matches current behavior.

## 3. Claim resolution algorithm

The algorithm should be deterministic and stage-aware.

### Important compatibility rule

The new engine should not mean:

> generate every possible claim, apply defeaters, sort, done

That would surface extra defeated candidates and fail parity.

Instead, it should mean:

> evaluate independent rules **within explicit stages** that mirror the current cascade.

### Resolver shape

```python
def resolve(
    ctx: SectionEvidenceContext,
    *,
    preemptive_positive_rules: tuple[RuleSpec[PositiveClaim], ...],
    primary_positive_rules: tuple[RuleSpec[PositiveClaim], ...],
    primary_sink_rules: tuple[RuleSpec[UnresolvedSink], ...],
    fallback_defeater_rules: tuple[RuleSpec[Defeater], ...],
    promotion_positive_rules: tuple[RuleSpec[PositiveClaim], ...],
    final_fallback_rules: tuple[RuleSpec[PositiveClaim], ...],
) -> ResolvedSectionClaims:
    ...
```

### Exact compatibility algorithm

```python
def resolve(...):
    selectable: list[tuple[int, SelectableCandidate]] = []
    suppressed: list[SuppressedCandidate] = []
    applied_defeaters: list[Defeater] = []

    # 1. Preemptive positives.
    preemptive = emit_all(ctx, preemptive_positive_rules)
    if preemptive:
        selectable.extend(preemptive)
        return finalize(ctx, selectable, suppressed, applied_defeaters)

    # 2. If this is not a replay-bug diagnosis path, we are done.
    if not ctx.is_replay_bug_diagnosis:
        # In practice this should usually be empty here, but keep it total.
        return finalize(ctx, selectable, suppressed, applied_defeaters)

    # 3. Primary replay-path positives and sinks.
    primary = emit_all(ctx, primary_positive_rules) + emit_all(ctx, primary_sink_rules)
    selectable.extend(primary)

    # 4. Latent fallback replay claim exists only on replay-diagnosis path.
    latent_fallback = emit_first(ctx, final_fallback_rules)  # usually replay_divergence

    # 5. Fallback defeaters only matter if primary emitted nothing.
    if not primary and latent_fallback is not None:
        for spec, defeater in emit_all(ctx, fallback_defeater_rules):
            if defeater.targets.matches(latent_fallback[1]):
                applied_defeaters.append(defeater)
                suppressed.append(
                    SuppressedCandidate(
                        claim=latent_fallback[1],
                        defeated_by_rule_id=defeater.rule_id,
                        defeated_by_inference_rule=defeater.inference_rule,
                        replacement_sink_kind=(
                            defeater.replacement_sink.kind
                            if defeater.replacement_sink is not None
                            else None
                        ),
                    )
                )
                latent_fallback = None
                if (
                    defeater.effect == DefeaterEffect.REPLACE_WITH_SINK
                    and defeater.replacement_sink is not None
                ):
                    selectable.append((spec.order, defeater.replacement_sink))
                break  # preserves current “first fallback sink wins” behavior

    # 6. Invariant-promotion positives always materialize on replay path.
    selectable.extend(emit_all(ctx, promotion_positive_rules))

    # 7. Final fallback only materializes if nothing selectable exists.
    if not selectable and latent_fallback is not None:
        selectable.append(latent_fallback)

    return finalize(ctx, selectable, suppressed, applied_defeaters)
```

### Finalization

```python
def finalize(...):
    ordered = sorted(
        selectable,
        key=lambda item: (
            SECTION_TIER_PRIORITY[item[1].tier],
            item[0],  # registry order, matches current append order
        ),
    )
    candidates = tuple(item[1] for item in ordered)
    selected = candidates[0]
    defeated = candidates[1:]
    return ResolvedSectionClaims(
        ctx=ctx,
        selected=selected,
        candidates=candidates,
        defeated_candidates=defeated,
        suppressed_candidates=tuple(suppressed),
        applied_defeaters=tuple(applied_defeaters),
    )
```

### How defeaters apply

For A1:

* **Do not mutate a claim’s tier in place.**
* A defeater either:

  * suppresses a claim, or
  * suppresses a claim and materializes an `UnresolvedSink`.

That is audit-clean and easy to diff.

### How final selection is made

Exactly by:

1. `SECTION_TIER_PRIORITY`
2. stable registry order

That reproduces the current stable-sort-by-tier behavior.

### How defeated claims are recorded

Two categories:

* **public defeated candidates**: selectable candidates that lost to the winner
* **internal suppressed candidates**: positive claims removed by defeaters before selection

For bit-identical compatibility, only the first category should feed the legacy `defeated_candidates` field.

## 4. Integration with `SectionEvidenceContext`

The new flow should be:

```python
contexts = build_section_contexts(...)
rows = []
for ctx in contexts.values():
    result = resolve(
        ctx,
        preemptive_positive_rules=PREEMPTIVE_POSITIVE_RULES,
        primary_positive_rules=PRIMARY_POSITIVE_RULES,
        primary_sink_rules=PRIMARY_SINK_RULES,
        fallback_defeater_rules=FALLBACK_DEFEATER_RULES,
        promotion_positive_rules=PROMOTION_POSITIVE_RULES,
        final_fallback_rules=FINAL_FALLBACK_RULES,
    )
    rows.append(result.to_legacy_row())
```

### What `SectionEvidenceContext` must provide

Rules should not do raw nested dict archaeology. They should read typed fields.

At minimum, the context needs:

* `section`
* `diagnosis`
* `blame_source`
* `similarity`
* `oracle_text`
* `replay_text`
* `oracle_content_absent`
* `oracle_suspect_detail`
* `oracle_range_match`
* `cross_chapter_oracle_match`
* `alternative_match`
* `baseline_alternative_match`
* `baseline_same_section_structure_drift`
* `bisect_support`
* `strict_verdict`
* `strict_payload_confidence`
* `invariant_violations`
* `has_timeline_entry`
* `has_extraction_gap`

And a few **derived booleans** so rules stay isolated:

```python
ctx.is_oracle_incorrect_diagnosis
ctx.is_replay_bug_diagnosis
ctx.has_preexisting_residue_support
ctx.has_negligible_preexisting_drop_support
ctx.has_source_barrier
ctx.has_recovery_barrier
```

That last point matters. Any current `if not candidates` branch that really means “a prior fact pattern exists” should be lifted into a typed context flag, not left as an inter-rule dependency.

## 5. Migration path

Do not direct-replace the old function first.

### Step 1

Freeze the old behavior.

Rename the current implementation:

```python
_legacy_build_section_claims(...)
```

Do not edit it except bugfixes required for parity tests.

### Step 2

Add the typed path.

Create:

* `build_section_claims_typed(...) -> tuple[ResolvedSectionClaims, ...]`
* `serialize_section_claim_rows(...) -> list[dict]`

Then make a new wrapper:

```python
def _build_section_claims(...):
    typed = build_section_claims_typed(...)
    return [row.to_legacy_row() for row in typed]
```

### Step 3

Dual-run in tests.

For every existing evidence test:

```python
legacy = _legacy_build_section_claims(...)
typed = [r.to_legacy_row() for r in build_section_claims_typed(...)]
assert typed == legacy
```

This should include:

* full row equality
* candidate ordering
* defeated candidate ordering
* support payload equality
* dict key insertion order if JSON snapshots compare textually

### Step 4

Add a corpus differential test.

Run both engines across a representative statute set, not just unit fixtures.

Fail on any diff.

### Step 5

Feature flag for one transition window.

```python
LAWVM_EVIDENCE_CLAIM_ENGINE=legacy|typed
```

Default legacy at first, typed in CI dual-run.

### Step 6

Flip default when parity is stable.

Keep legacy implementation for one cleanup cycle, then delete it.

## 6. Output format

### Default output

**No public format change in A1.**

Keep:

* `selected_kind`
* `selected_tier`
* `selected_inference_rule`
* `candidates`
* `defeated_candidates`
* `candidate_count`
* `candidate_kinds`
* `defeated_candidate_kinds`

exactly as they are now.

### Internal output

The new typed API should additionally expose:

* `selected.rule_id`
* `applied_defeaters`
* `suppressed_candidates`

This provides machine-inspectable resolution without changing the external JSON.

### Optional later extension

Once parity is locked, add non-breaking debug fields behind a flag:

* `selected_rule_id`
* `applied_defeater_rule_ids`
* `suppressed_candidate_rule_ids`

But not in the default serializer yet.

## Initial implementation order

For a directly implementable and parity-safe migration:

1. add the dataclasses above
2. move each current branch into a named rule function
3. create the staged registries
4. implement the staged resolver with a **latent fallback claim**
5. keep public serialization bit-identical
6. dual-run until there are zero diffs

The most important design choice is this one:

> **Defeaters should target proof obligations via tags, but in A1 compatibility mode they should only defeat the latent fallback replay claim unless today’s behavior clearly says otherwise.**

This provides the proof-claim algebra without silently changing how current section claims are selected.

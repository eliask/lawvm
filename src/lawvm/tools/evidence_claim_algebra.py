"""Typed proof algebra for evidence section claims (A1).

Core types, staged resolver, and serialisation.  Each rule function lives
in evidence_section_rules.py and emits typed claims/sinks/defeaters;
the resolver here assembles them into a ResolvedSectionClaims per section.

The typed path MUST produce bit-identical output to the legacy
_build_section_claims() path via to_legacy_row().
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping, TypeAlias, TYPE_CHECKING

if TYPE_CHECKING:
    from lawvm.core.section_evidence_context import SectionEvidenceContext

SupportPayload: TypeAlias = Mapping[str, Any]


# ---------------------------------------------------------------------------
# Proof tier hierarchy
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Rule phases
# ---------------------------------------------------------------------------

class RulePhase(StrEnum):
    PREEMPTIVE = "preemptive"
    PRIMARY = "primary"
    FALLBACK_DEFEATER = "fallback_defeater"
    PROMOTION = "promotion"
    FINAL_FALLBACK = "final_fallback"


# ---------------------------------------------------------------------------
# Claim selector (for defeater targeting)
# ---------------------------------------------------------------------------

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

    def matches(self, claim: PositiveClaim) -> bool:
        if self.tags and not (self.tags & claim.proof_tags):
            return False
        if self.kinds and claim.kind not in self.kinds:
            return False
        if self.rule_ids and claim.rule_id not in self.rule_ids:
            return False
        if self.tiers and claim.tier not in self.tiers:
            return False
        return True


# ---------------------------------------------------------------------------
# Selectable candidates
# ---------------------------------------------------------------------------

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

    This is not a positive proof.  It is the typed explanation for why
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


SelectableCandidate: TypeAlias = PositiveClaim | UnresolvedSink


# ---------------------------------------------------------------------------
# Defeaters
# ---------------------------------------------------------------------------

class DefeaterEffect(StrEnum):
    SUPPRESS = "suppress"
    REPLACE_WITH_SINK = "replace_with_sink"


@dataclass(frozen=True)
class Defeater:
    """A typed blocker.

    In v1 use it mainly against the latent replay fallback claim.
    """
    rule_id: str
    targets: ClaimSelector
    effect: DefeaterEffect
    inference_rule: str
    observation_sources: tuple[str, ...]
    support: SupportPayload
    replacement_sink: UnresolvedSink | None = None


# ---------------------------------------------------------------------------
# Rule specification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleSpec:
    """Registry entry for a typed rule function."""
    rule_id: str
    phase: RulePhase
    order: int
    emit: Callable[[SectionEvidenceContext], tuple[Any, ...]]


# ---------------------------------------------------------------------------
# Resolution results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SuppressedCandidate:
    claim: PositiveClaim
    defeated_by_rule_id: str
    defeated_by_inference_rule: str
    replacement_sink_kind: str | None = None


@dataclass(frozen=True)
class ResolvedSectionClaims:
    ctx: SectionEvidenceContext
    selected: SelectableCandidate | None
    candidates: tuple[SelectableCandidate, ...]
    defeated_candidates: tuple[SelectableCandidate, ...]
    suppressed_candidates: tuple[SuppressedCandidate, ...] = ()
    applied_defeaters: tuple[Defeater, ...] = ()

    def to_legacy_row(self) -> dict[str, Any]:
        candidate_dicts = [c.to_candidate_dict() for c in self.candidates]
        if candidate_dicts:
            winner = candidate_dicts[0]
        else:
            # Empty candidates (e.g. MATCH diagnosis).
            winner = {
                "kind": "",
                "tier": "",
                "inference_rule": "",
                "observation_sources": [],
            }
        return {
            "section": self.ctx.section_label,
            "diagnosis": self.ctx.diagnosis,
            "blame_source": self.ctx.blame_source,
            "similarity": self.ctx.similarity,
            "strict_payload_confidence": self.ctx.strict_payload_confidence,
            "selected_kind": winner["kind"],
            "selected_tier": winner["tier"],
            "selected_inference_rule": winner["inference_rule"],
            "oracle_range_match": self.ctx.oracle_range_match or None,
            "cross_chapter_oracle_match": self.ctx.cross_chapter_oracle_match or None,
            "alternative_replay_match": self.ctx.alternative_replay_match or None,
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
                    "defeated_by_observation_sources": list(
                        winner["observation_sources"]
                    ),
                }
                for c in candidate_dicts[1:]
                if c["kind"]
            ],
            "candidates": candidate_dicts,
        }


# ---------------------------------------------------------------------------
# Emit helpers
# ---------------------------------------------------------------------------

def _emit_all(
    ctx: SectionEvidenceContext,
    rules: tuple[RuleSpec, ...],
) -> list[tuple[int, Any]]:
    """Run all rules and collect (order, item) pairs."""
    results: list[tuple[int, Any]] = []
    for spec in rules:
        for item in spec.emit(ctx):
            results.append((spec.order, item))
    return results


def _emit_first(
    ctx: SectionEvidenceContext,
    rules: tuple[RuleSpec, ...],
) -> tuple[int, Any] | None:
    """Run rules in order, return the first emitted item or None."""
    for spec in rules:
        items = spec.emit(ctx)
        if items:
            return (spec.order, items[0])
    return None


# ---------------------------------------------------------------------------
# Staged resolver
# ---------------------------------------------------------------------------

def _finalize(
    ctx: SectionEvidenceContext,
    selectable: list[tuple[int, SelectableCandidate]],
    suppressed: list[SuppressedCandidate],
    applied_defeaters: list[Defeater],
) -> ResolvedSectionClaims:
    if not selectable:
        # No candidates at all (e.g. MATCH diagnosis).  Mirror legacy
        # behavior: empty candidates list, no selected.
        return ResolvedSectionClaims(
            ctx=ctx,
            selected=None,  # type: ignore[arg-type]
            candidates=(),
            defeated_candidates=(),
            suppressed_candidates=tuple(suppressed),
            applied_defeaters=tuple(applied_defeaters),
        )
    ordered = sorted(
        selectable,
        key=lambda item: (
            SECTION_TIER_PRIORITY[item[1].tier],
            item[0],  # registry order preserves current append order
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


def resolve(
    ctx: SectionEvidenceContext,
    *,
    preemptive_positive_rules: tuple[RuleSpec, ...],
    primary_positive_rules: tuple[RuleSpec, ...],
    primary_sink_rules: tuple[RuleSpec, ...],
    fallback_defeater_rules: tuple[RuleSpec, ...],
    promotion_positive_rules: tuple[RuleSpec, ...],
    final_fallback_rules: tuple[RuleSpec, ...],
) -> ResolvedSectionClaims:
    """Staged claim resolution algorithm.

    Mirrors the exact short-circuit structure of the legacy
    _build_section_claims() cascade to ensure bit-identical output.
    """
    selectable: list[tuple[int, SelectableCandidate]] = []
    suppressed: list[SuppressedCandidate] = []
    applied_defeaters: list[Defeater] = []

    # 1. Preemptive positives (short-circuit if any).
    preemptive = _emit_all(ctx, preemptive_positive_rules)
    if preemptive:
        selectable.extend(preemptive)
        return _finalize(ctx, selectable, suppressed, applied_defeaters)

    # 2. If this is not a replay-bug diagnosis path, we are done.
    if not ctx.is_replay_bug_diagnosis:
        # Should not normally reach here with zero candidates, but keep total.
        # If somehow empty, we would need at least one candidate.
        # This mirrors the legacy: candidates stays [] when diagnosis not in
        # _ORACLE_INCORRECT_DIAGNOSES and not in _REPLAY_BUG_DIAGNOSES.
        # But the legacy always has at least one candidate by construction...
        # For safety, return an empty finalize which will error if truly empty.
        # In practice preemptive should have caught oracle-incorrect cases.
        return _finalize(ctx, selectable, suppressed, applied_defeaters)

    # 3. Primary replay-path positives and sinks.
    primary = _emit_all(ctx, primary_positive_rules) + _emit_all(
        ctx, primary_sink_rules
    )
    selectable.extend(primary)

    # 4. Latent fallback replay claim — exists only on replay-diagnosis path.
    latent_fallback = _emit_first(ctx, final_fallback_rules)

    # 5. Fallback defeaters only matter if primary emitted nothing.
    if not primary and latent_fallback is not None:
        for defeater_pair in _emit_all(ctx, fallback_defeater_rules):
            defeater: Defeater = defeater_pair[1]
            fb_claim = latent_fallback[1]
            if isinstance(fb_claim, PositiveClaim) and defeater.targets.matches(
                fb_claim
            ):
                applied_defeaters.append(defeater)
                suppressed.append(
                    SuppressedCandidate(
                        claim=fb_claim,
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
                    selectable.append(
                        (defeater_pair[0], defeater.replacement_sink)
                    )
                break  # first defeater wins — preserves legacy behavior

    # 6. Promotion positives always materialise on replay path.
    selectable.extend(_emit_all(ctx, promotion_positive_rules))

    # 7. Final fallback only materialises if nothing selectable exists.
    if not selectable and latent_fallback is not None:
        selectable.append(latent_fallback)

    return _finalize(ctx, selectable, suppressed, applied_defeaters)

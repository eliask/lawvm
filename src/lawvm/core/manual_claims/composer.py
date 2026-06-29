"""Composer: derive ClaimCompositionDecision per-build (v2.2 design memo §4.4 + §6).

This is the ONLY code path that produces ClaimCompositionDecision records.
Claims cannot self-authorize. The composer reads:
  - ManualCompilationClaim  (immutable claim content)
  - ClaimState              (current lifecycle state)
  - StrictProfile           (authoritative profile for new callers)
  - ProfileTag              (deprecated compatibility profile label)
  - build_id                (stable build identifier)
  - PrecedenceRegistry      (operator-authored precedence rules)

and derives authorization deterministically from those inputs.

No ClaimCompositionDecision is ever written to a claim file. Composer runs at
projection-emission time, writes the decision to the event log (event_kind=
"composed_for_build"), and returns the decision to the caller.

§14 adversary finding: strict-mode boundary leak prevention.
  Every call to derive_composition_decision emits a ClaimStateEvent with
  event_kind="composed_for_build" carrying build_id, profile, authorized, and
  reason_code. Audit trail is complete regardless of authorization result.

Design:
  - Pure function: same inputs → same output (deterministic per build).
  - No side effects; caller is responsible for persisting the event.
  - AGENTS.md §1.9: no getattr / stringly-typed operations.
  - feedback_frozen_for_fp_not_serialization: only frozen dataclasses.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from lawvm.core.compile_result import StrictProfile
from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec
from lawvm.core.manual_claims.primitive import (
    ClaimLayer,
    ClaimCompositionDecision,
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    ManualCompilationClaim,
    Producer,
    ReviewStatus,
    SourceWitnessType,
    ValidatorStatus,
    _ProfileTagDeprecated as ProfileTag,
)
from lawvm.core.manual_claims.precedence import PrecedenceRegistry
from lawvm.core.phase_replay_gate import PhaseLocalReplayGate, PhaseReplayGateEvaluation


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_ATTESTED_WITNESS_TYPES = frozenset({
    SourceWitnessType.FINLEX_CORRIGENDUM,
    SourceWitnessType.EXTERNAL_ARCHIVAL,
    SourceWitnessType.OPERATOR_FILING,
})

_STRICT_VALIDATOR_STATUSES = frozenset({
    ValidatorStatus.SPAN_VERIFIED,
    ValidatorStatus.ENTAILMENT_VERIFIED,
    ValidatorStatus.MIGRATION_REVALIDATED,
})


def _is_semantic_compilation(claim_kind: str) -> bool:
    """Return True if this claim kind is tagged as a semantic compilation claim.

    Checked against the kind registry. INLINE_STATUTE_RESOLUTION is NOT one.
    Returns False for unknown/unregistered kinds (fail-safe default).
    """
    spec = get_claim_kind_spec(claim_kind)
    if spec is None:
        return False
    return spec.is_semantic_compilation_claim


def _make_decision(
    claim: ManualCompilationClaim,
    build_id: str,
    profile: ProfileTag,
    authorized: bool,
    reason_code: str,
    replay_authorized: bool,
) -> ClaimCompositionDecision:
    return ClaimCompositionDecision(
        claim_id=claim.claim_id,
        build_id=build_id,
        profile=profile,
        authorized=authorized,
        reason_code=reason_code,
        replay_authorized=replay_authorized,
    )


def _make_composed_event(
    claim: ManualCompilationClaim,
    build_id: str,
    profile: ProfileTag,
    authorized: bool,
    reason_code: str,
    producer: Producer,
    strict_profile_name: str = "",
) -> ClaimStateEvent:
    """Emit a ClaimStateEvent for each composition decision (authorized or not).

    event_kind="composed_for_build" per §4.3 + §14.
    No status transition: old_status and new_status are both None.
    The reason payload carries build_id + profile + authorized + reason_code as JSON.
    """
    reason_payload = json.dumps({
        "build_id": build_id,
        "profile": profile.value,
        **({"strict_profile": strict_profile_name} if strict_profile_name else {}),
        "authorized": authorized,
        "reason_code": reason_code,
    })
    return ClaimStateEvent(
        claim_id=claim.claim_id,
        event_kind="composed_for_build",
        timestamp=datetime.now(tz=timezone.utc),
        producer=producer,
        old_status=None,
        new_status=None,
        reason=reason_payload,
    )


# ---------------------------------------------------------------------------
# Main composer
# ---------------------------------------------------------------------------


_COMPOSER_PRODUCER = Producer(
    producer_kind="tool",
    handle=None,
    model_id=None,
    timestamp=datetime(2026, 6, 4, 0, 0, 0, tzinfo=timezone.utc),
    environment="lawvm-composer",
)


def derive_composition_decision(
    claim: ManualCompilationClaim,
    state: ClaimState,
    profile: ProfileTag,
    build_id: str,
    precedence_registry: PrecedenceRegistry,
    phase_replay_gate: PhaseLocalReplayGate | None = None,
) -> tuple[ClaimCompositionDecision, ClaimStateEvent]:
    """Composer-emitted authorization decision. NEVER author-set.

    Returns (ClaimCompositionDecision, ClaimStateEvent). The caller persists
    the event to the event log. The decision drives projection emission.

    Per §6 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2:

    deterministic_only:
      authorized=False for ALL claims. The deterministic extractor rows are
      authoritative; no claim-derived rows enter this profile.

    strict_with_attested_claims:
      authorized=True requires ALL of:
        - state.status == accepted
        - state.review_status == verified_manual
        - state.validator_status in {span_verified, entailment_verified, migration_revalidated}
        - source_witness_type in {finlex_corrigendum, external_archival, operator_filing}
          OR (source_witness_type == llm_proposal AND review_status == verified_manual)
        - For semantic compilation claims: additionally replay_authorized=True
          (not yet wired to a real validator; returns False for unknown kinds)

    non_strict_with_claims:
      As strict but WITHOUT the review_status == verified_manual requirement.
      entailment_verified LLM proposals are admissible without manual verification.

    exploratory:
      Accepts any claim with state.status == accepted.
      Semantic compilation claims STILL require replay_authorized=True.
    """
    # Build a stable composer producer with the correct timestamp
    producer = Producer(
        producer_kind="tool",
        handle=None,
        model_id=None,
        timestamp=datetime.now(tz=timezone.utc),
        environment="lawvm-composer",
    )

    decision, event = _derive(
        claim,
        state,
        profile,
        build_id,
        producer,
        precedence_registry,
        phase_replay_gate=phase_replay_gate,
    )
    return decision, event


def derive_composition_decision_for_strict_profile(
    claim: ManualCompilationClaim,
    state: ClaimState,
    strict_profile: StrictProfile,
    build_id: str,
    precedence_registry: PrecedenceRegistry,
    phase_replay_gate: PhaseLocalReplayGate | None = None,
) -> tuple[ClaimCompositionDecision, ClaimStateEvent]:
    """Composer decision where StrictProfile is the source of authority.

    The returned compatibility decision still contains a deprecated ProfileTag
    because ``ClaimCompositionDecision`` is a v2.2 transition record. The tag is
    derived from StrictProfile channel policy, not chosen by the caller.
    """

    producer = Producer(
        producer_kind="tool",
        handle=None,
        model_id=None,
        timestamp=datetime.now(tz=timezone.utc),
        environment="lawvm-composer",
    )
    compatibility_profile, blocker = compatibility_profile_for_strict_profile(
        claim,
        strict_profile,
    )
    if blocker:
        decision = _make_decision(
            claim,
            build_id,
            ProfileTag.DETERMINISTIC_ONLY,
            False,
            blocker,
            False,
        )
        event = _make_composed_event(
            claim,
            build_id,
            ProfileTag.DETERMINISTIC_ONLY,
            False,
            blocker,
            producer,
            strict_profile_name=strict_profile.name,
        )
        return decision, event
    return _derive(
        claim,
        state,
        compatibility_profile,
        build_id,
        producer,
        precedence_registry,
        strict_profile_name=strict_profile.name,
        phase_replay_gate=phase_replay_gate,
    )


def compatibility_profile_for_strict_profile(
    claim: ManualCompilationClaim,
    strict_profile: StrictProfile,
) -> tuple[ProfileTag, str]:
    """Return deprecated compatibility label plus a blocker reason."""

    channel = _claim_attestation_channel(claim)
    if not _strict_profile_allows_channel(strict_profile, channel):
        return ProfileTag.DETERMINISTIC_ONLY, "strict_profile_disallows_attested_channel"
    if strict_profile.allows_unreviewed_llm_attestations:
        return ProfileTag.NON_STRICT_WITH_CLAIMS, ""
    return ProfileTag.STRICT_WITH_ATTESTED_CLAIMS, ""


def _claim_attestation_channel(claim: ManualCompilationClaim) -> str:
    if _is_semantic_compilation(claim.claim_kind):
        return "semantic_compilation"
    if claim.claim_kind == "fi.v1.INLINE_STATUTE_RESOLUTION":
        return "reference_resolution"
    if claim.claim_layer == ClaimLayer.SUBSTRATE:
        return "surface_extraction"
    if claim.claim_layer == ClaimLayer.EXTRACTION:
        return "surface_extraction"
    if claim.claim_layer == ClaimLayer.CORRECTION:
        return "source_correction"
    if claim.claim_layer == ClaimLayer.ADJUDICATION:
        return "ambiguity_adjudication"
    return "unknown"


def _strict_profile_allows_channel(strict_profile: StrictProfile, channel: str) -> bool:
    channel_flags = {
        "reference_resolution": strict_profile.allows_attested_reference_resolution,
        "surface_extraction": strict_profile.allows_attested_surface_extraction,
        "source_correction": strict_profile.allows_attested_source_correction,
        "target_selection": strict_profile.allows_attested_target_selection,
        "semantic_compilation": strict_profile.allows_attested_semantic_compilation,
        "ambiguity_adjudication": strict_profile.allows_attested_ambiguity_adjudication,
        "oracle_adjudication": strict_profile.allows_attested_oracle_adjudication,
        "unknown": False,
    }
    return channel_flags[channel]


def _derive(
    claim: ManualCompilationClaim,
    state: ClaimState,
    profile: ProfileTag,
    build_id: str,
    producer: Producer,
    precedence_registry: PrecedenceRegistry,
    strict_profile_name: str = "",
    phase_replay_gate: PhaseLocalReplayGate | None = None,
) -> tuple[ClaimCompositionDecision, ClaimStateEvent]:
    """Inner derivation — separated to make testing producer-independent."""

    def _reject(reason_code: str) -> tuple[ClaimCompositionDecision, ClaimStateEvent]:
        dec = _make_decision(claim, build_id, profile, False, reason_code, False)
        evt = _make_composed_event(
            claim,
            build_id,
            profile,
            False,
            reason_code,
            producer,
            strict_profile_name=strict_profile_name,
        )
        return dec, evt

    def _accept(reason_code: str, replay_auth: bool = False) -> tuple[ClaimCompositionDecision, ClaimStateEvent]:
        dec = _make_decision(claim, build_id, profile, True, reason_code, replay_auth)
        evt = _make_composed_event(
            claim,
            build_id,
            profile,
            True,
            reason_code,
            producer,
            strict_profile_name=strict_profile_name,
        )
        return dec, evt

    # --- deterministic_only: no claims consumed ---
    if profile == ProfileTag.DETERMINISTIC_ONLY:
        return _reject("deterministic_only_profile")

    # --- all other profiles: must be accepted status ---
    if state.claim_state_status != ClaimStatus.ACCEPTED:
        return _reject("rejected_not_accepted_status")

    # --- exploratory: accept any accepted claim (semantic still needs replay_authorized) ---
    if profile == ProfileTag.EXPLORATORY:
        is_sem = _is_semantic_compilation(claim.claim_kind)
        if is_sem:
            gate = _semantic_phase_replay_gate_evaluation(claim, phase_replay_gate)
            if not gate.replay_authorized:
                return _reject(gate.reason_code)
            return _accept("exploratory_accepted_phase_replay_authorized", replay_auth=True)
        return _accept("exploratory_accepted", replay_auth=False)

    # --- strict and non_strict: check validator status ---
    if state.validator_status not in _STRICT_VALIDATOR_STATUSES:
        return _reject("rejected_unvalidated")

    # --- check source witness + review requirements ---
    is_llm = claim.source_witness_type == SourceWitnessType.LLM_PROPOSAL
    is_attested = claim.source_witness_type in _ATTESTED_WITNESS_TYPES

    if profile == ProfileTag.STRICT_WITH_ATTESTED_CLAIMS:
        if is_llm:
            if state.review_status != ReviewStatus.VERIFIED_MANUAL:
                return _reject("rejected_unreviewed_llm")
        elif not is_attested:
            # finlex_akn claims go through deterministic path, not here
            return _reject("rejected_non_attested_witness")
        if state.review_status != ReviewStatus.VERIFIED_MANUAL:
            return _reject("rejected_unreviewed_llm")

    elif profile == ProfileTag.NON_STRICT_WITH_CLAIMS:
        if is_llm:
            # Entailment-verified LLM proposals are admissible without manual verification.
            # second_pass_correlated alone is NOT enough.
            if state.validator_status != ValidatorStatus.ENTAILMENT_VERIFIED:
                if state.review_status != ReviewStatus.VERIFIED_MANUAL:
                    return _reject("rejected_unreviewed_llm")
        elif not is_attested:
            return _reject("rejected_non_attested_witness")

    # --- semantic compilation claims need replay_authorized ---
    is_sem = _is_semantic_compilation(claim.claim_kind)
    if is_sem:
        gate = _semantic_phase_replay_gate_evaluation(claim, phase_replay_gate)
        if not gate.replay_authorized:
            return _reject(gate.reason_code)
        reason = (
            "accepted_strict_attested_phase_replay_authorized"
            if profile == ProfileTag.STRICT_WITH_ATTESTED_CLAIMS
            else "accepted_non_strict_phase_replay_authorized"
        )
        return _accept(reason, replay_auth=True)

    # --- all checks passed ---
    reason = (
        "accepted_strict_attested"
        if profile == ProfileTag.STRICT_WITH_ATTESTED_CLAIMS
        else "accepted_non_strict"
    )
    return _accept(reason, replay_auth=False)


def _semantic_phase_replay_gate_evaluation(
    claim: ManualCompilationClaim,
    phase_replay_gate: PhaseLocalReplayGate | None,
) -> PhaseReplayGateEvaluation:
    if phase_replay_gate is None:
        return PhaseReplayGateEvaluation(False, "rejected_replay_authorized_false")
    return phase_replay_gate.evaluate_for_claim(
        claim_id=claim.claim_id,
        claim_kind=claim.claim_kind,
        frontier_ref=_claim_frontier_ref(claim),
    )


def _claim_frontier_ref(claim: ManualCompilationClaim) -> str:
    target = dict(claim.target)
    return str(target.get("frontier_ref") or "")

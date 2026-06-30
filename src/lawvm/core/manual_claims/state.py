"""Event-log → current-state projection.

The event log (events.jsonl) is the source of truth. This module provides
the projection function: given a sequence of ClaimStateEvents for one claim_id,
return the current ClaimState.

Design:
  - Pure function: project_state(events) -> ClaimState.
  - Idempotent: can be called at any time to rebuild state from events.
  - State files in states/current/ are optional materialized checkpoints.
  - Initial event must be event_kind='proposed'.
  - Subsequent events update status and review/validator status.

State machine (§7 of design memo):
  proposed → accepted | rejected
  accepted → retracted | superseded | needs_revalidation
  needs_revalidation → accepted | orphaned
  accepted/rejected/retracted/superseded/orphaned = terminal (no further transitions)
  orphaned = terminal
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from lawvm.core.manual_claims.primitive import (
    ClaimConfidence,
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    ReviewStatus,
    ValidatorStatus,
)


# ---------------------------------------------------------------------------
# Allowed transitions (source → set of allowed targets)
# ---------------------------------------------------------------------------

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"accepted", "rejected"}),
    "accepted": frozenset({"retracted", "superseded", "needs_revalidation"}),
    "needs_revalidation": frozenset({"accepted", "orphaned"}),
    "rejected": frozenset(),
    "retracted": frozenset(),
    "superseded": frozenset(),
    "orphaned": frozenset(),
}


def _validate_transition(old_status: Optional[str], new_status: str) -> None:
    """Raise ValueError if the status transition is not allowed."""
    if old_status is None:
        if new_status != "proposed":
            raise ValueError(
                f"First event must transition to 'proposed', got {new_status!r}"
            )
        return
    allowed = _ALLOWED_TRANSITIONS.get(old_status, frozenset())
    if new_status not in allowed:
        raise ValueError(
            f"Invalid status transition: {old_status!r} → {new_status!r}. "
            f"Allowed: {sorted(allowed)}"
        )


# ---------------------------------------------------------------------------
# Review/validator status updates from event kinds
# ---------------------------------------------------------------------------

_EVENT_KIND_TO_REVIEW_STATUS: dict[str, Optional[ReviewStatus]] = {
    "proposed": ReviewStatus.PROPOSED,
    "accepted": ReviewStatus.VERIFIED_MANUAL,
    "rejected": None,
    "retracted": None,
    "superseded": None,
    "needs_revalidation": None,
    "revalidated": None,
    "orphaned": None,
    "consumed": None,
}

_EVENT_KIND_TO_VALIDATOR_STATUS: dict[str, Optional[ValidatorStatus]] = {
    "proposed": ValidatorStatus.UNVALIDATED,
    "revalidated": ValidatorStatus.MIGRATION_REVALIDATED,
}


# ---------------------------------------------------------------------------
# Projection function
# ---------------------------------------------------------------------------


def project_state(
    claim_id: str,
    events: Sequence[ClaimStateEvent],
    initial_confidence: ClaimConfidence = ClaimConfidence.MEDIUM,
) -> Optional[ClaimState]:
    """Project the current ClaimState from a sequence of events.

    Returns None if events is empty (claim not yet filed).
    Raises ValueError on invalid transitions or events for wrong claim_id.

    The initial_confidence is set from the 'proposed' event; subsequent
    events do not change confidence (that requires a new claim supersession).
    """
    if not events:
        return None

    current_status: Optional[str] = None
    current_review_status = ReviewStatus.PROPOSED
    current_validator_status = ValidatorStatus.UNVALIDATED
    current_confidence = initial_confidence
    last_updated: Optional[datetime] = None

    for event in events:
        if event.claim_id != claim_id:
            raise ValueError(
                f"Event for wrong claim_id: expected {claim_id!r}, "
                f"got {event.claim_id!r}"
            )

        new_status = event.new_status
        if new_status is not None:
            _validate_transition(current_status, new_status)
            current_status = new_status

        # Update review status from event kind
        rs = _EVENT_KIND_TO_REVIEW_STATUS.get(event.event_kind)
        if rs is not None:
            current_review_status = rs

        # Update validator status from event kind
        vs = _EVENT_KIND_TO_VALIDATOR_STATUS.get(event.event_kind)
        if vs is not None:
            current_validator_status = vs

        # Span/entailment validator events
        if event.event_kind == "span_verified":
            current_validator_status = ValidatorStatus.SPAN_VERIFIED
        elif event.event_kind == "entailment_verified":
            current_validator_status = ValidatorStatus.ENTAILMENT_VERIFIED

        last_updated = event.timestamp

    if current_status is None:
        return None

    assert last_updated is not None
    return ClaimState(
        claim_id=claim_id,
        claim_state_status=ClaimStatus(current_status),
        review_status=current_review_status,
        validator_status=current_validator_status,
        confidence=current_confidence,
        last_updated=last_updated,
    )

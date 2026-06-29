"""Tests for manual claims primitive types and enums.

Covers:
  - test_review_status_mutation_preserves_claim_id (LOAD-BEARING per ChatGPT Pro)
  - test_claim_id_includes_schema_version_in_hash_domain
  - test_load_time_hash_mismatch_rejected
  - test_event_log_is_append_only
  - test_self_authorization_impossible
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from lawvm.core.manual_claims.hashing import compute_claim_id, verify_claim_id
from lawvm.core.manual_claims.primitive import (
    ClaimConfidence,
    ClaimLayer,
    ClaimScope,
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    ManualCompilationClaim,
    Producer,
    ProfileTag,
    ReviewStatus,
    SourceLocator,
    SourceWitnessType,
    ValidatorStatus,
)
from lawvm.core.manual_claims.state import project_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_producer() -> Producer:
    return Producer(
        producer_kind="operator",
        handle="test",
        model_id=None,
        timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        environment="test",
    )


def _make_locator() -> SourceLocator:
    return SourceLocator(
        artifact_kind="finlex_akn",
        statute_id="711/2022",
        he_id=None,
        version_id=None,
    )


def _make_scope(statute_id: str = "711/2022") -> ClaimScope:
    return ClaimScope(
        statute_id=statute_id,
        provision_ref="section:3",
        valid_at_start=date(2022, 1, 1),
        valid_at_end=None,
    )


def _make_claim(
    schema_version: str = "v1",
    jurisdiction: str = "fi",
    rationale: str = "test rationale",
) -> ManualCompilationClaim:
    """Build a minimal ManualCompilationClaim with the correct claim_id."""
    # Build without claim_id first, then compute
    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version=schema_version,
        jurisdiction=jurisdiction,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=_make_scope(),
        target=(
            ("statute_id", "711/2022"),
            ("mention_span", (100, 120)),
        ),
        value=(
            ("resolved_statute_id", "1234/2020"),
            ("citation_form", "lain 1234/2020"),
        ),
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_make_producer(),
        cited_source_locator=_make_locator(),
        cited_source_span=(100, 120),
        cited_source_hash="a" * 64,
        dependency_fingerprint=(("target_hash", "abc123"),),
        valid_at=(date(2022, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale=rationale,
    )
    # Compute actual claim_id
    claim_id = compute_claim_id(partial)
    return ManualCompilationClaim(
        claim_id=claim_id,
        schema_version=schema_version,
        jurisdiction=jurisdiction,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=_make_scope(),
        target=(
            ("statute_id", "711/2022"),
            ("mention_span", (100, 120)),
        ),
        value=(
            ("resolved_statute_id", "1234/2020"),
            ("citation_form", "lain 1234/2020"),
        ),
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_make_producer(),
        cited_source_locator=_make_locator(),
        cited_source_span=(100, 120),
        cited_source_hash="a" * 64,
        dependency_fingerprint=(("target_hash", "abc123"),),
        valid_at=(date(2022, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale=rationale,
    )


def _make_event(
    claim_id: str,
    event_kind: str = "proposed",
    old_status: str | None = None,
    new_status: str | None = "proposed",
) -> ClaimStateEvent:
    return ClaimStateEvent(
        claim_id=claim_id,
        event_kind=event_kind,
        timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        producer=_make_producer(),
        old_status=old_status,
        new_status=new_status,
        reason="test",
    )


# ---------------------------------------------------------------------------
# Test: review_status mutation preserves claim_id (LOAD-BEARING)
# ---------------------------------------------------------------------------


def test_review_status_mutation_preserves_claim_id():
    """Creating a new ClaimState with different review_status MUST NOT change claim_id.

    This is the structural load-bearing test per ChatGPT Pro. The claim_id is
    derived from ManualCompilationClaim fields only. ClaimState is a separate
    record. Changing review_status writes a new ClaimState row, not a new claim.
    """
    claim = _make_claim()
    original_claim_id = claim.claim_id

    # Simulate the lifecycle: proposed → verified_manual
    state_proposed = ClaimState(
        claim_id=claim.claim_id,
        claim_state_status=ClaimStatus.PROPOSED,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.UNVALIDATED,
        confidence=ClaimConfidence.MEDIUM,
        last_updated=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
    )

    state_reviewed = ClaimState(
        claim_id=claim.claim_id,
        claim_state_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.UNVALIDATED,
        confidence=ClaimConfidence.MEDIUM,
        last_updated=datetime(2026, 6, 4, 13, 0, 0, tzinfo=timezone.utc),
    )

    # claim_id must be unchanged in both states
    assert state_proposed.claim_id == original_claim_id
    assert state_reviewed.claim_id == original_claim_id

    # The claim itself is also unchanged
    assert claim.claim_id == original_claim_id

    # Recomputing claim_id from the claim still gives the same value
    assert compute_claim_id(claim) == original_claim_id

    # ClaimState.review_status changed, but claim.claim_id did not
    assert state_proposed.review_status == ReviewStatus.PROPOSED
    assert state_reviewed.review_status == ReviewStatus.VERIFIED_MANUAL
    assert claim.claim_id == original_claim_id


# ---------------------------------------------------------------------------
# Test: schema_version in hash domain
# ---------------------------------------------------------------------------


def test_claim_id_includes_schema_version_in_hash_domain():
    """Two otherwise-identical claims with different schema_version → different claim_ids."""
    claim_v1 = _make_claim(schema_version="v1")
    claim_v2 = _make_claim(schema_version="v2")

    assert claim_v1.schema_version == "v1"
    assert claim_v2.schema_version == "v2"
    assert claim_v1.claim_id != claim_v2.claim_id

    # Both must pass their own hash check
    verify_claim_id(claim_v1)
    verify_claim_id(claim_v2)


# ---------------------------------------------------------------------------
# Test: load-time hash mismatch rejected
# ---------------------------------------------------------------------------


def test_load_time_hash_mismatch_rejected(tmp_path: Path):
    """Write a claim, tamper with its rationale, reload → load-time hash check rejects."""
    from lawvm.core.manual_claims.storage import ClaimStore

    store = ClaimStore(tmp_path / "manual_claims")
    claim = _make_claim()
    store.write_claim(claim)

    # Tamper: change rationale in the JSON file
    claim_path = store._objects_dir / f"{claim.claim_id}.json"
    raw = json.loads(claim_path.read_text())
    raw["rationale"] = "TAMPERED RATIONALE"
    claim_path.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="Claim ID mismatch"):
        store.read_claim(claim.claim_id)


# ---------------------------------------------------------------------------
# Test: event log is append-only
# ---------------------------------------------------------------------------


def test_event_log_is_append_only(tmp_path: Path):
    """Events are appended, never modified. State machine reconstructs from event log."""
    from lawvm.core.manual_claims.storage import ClaimStore

    store = ClaimStore(tmp_path / "manual_claims")
    claim = _make_claim()
    store.write_claim(claim)

    e1 = _make_event(claim.claim_id, "proposed", None, "proposed")
    store.append_event(e1)

    e2 = _make_event(claim.claim_id, "accepted", "proposed", "accepted")
    store.append_event(e2)

    # Read events — both must be present, in order
    events = list(store.read_events(claim.claim_id))
    assert len(events) == 2
    assert events[0].event_kind == "proposed"
    assert events[1].event_kind == "accepted"

    # Verify events.jsonl has exactly 2 lines (no modification, only appends)
    lines = store._events_path.read_text().strip().split("\n")
    assert len(lines) == 2

    # Project state from event log
    state = project_state(claim.claim_id, events)
    assert state is not None
    assert state.claim_state_status == ClaimStatus.ACCEPTED
    assert state.review_status == ReviewStatus.VERIFIED_MANUAL

    # Append a third event — existing lines must be unchanged
    raw_before = store._events_path.read_text()
    e3 = _make_event(claim.claim_id, "retracted", "accepted", "retracted")
    store.append_event(e3)
    raw_after = store._events_path.read_text()

    # The original content is a prefix of the new content
    assert raw_after.startswith(raw_before)
    assert len(raw_after) > len(raw_before)


# ---------------------------------------------------------------------------
# Test: self-authorization impossible
# ---------------------------------------------------------------------------


def test_self_authorization_impossible():
    """A claim file asserting review_status=verified_manual cannot self-promote.

    ClaimState is separate from ManualCompilationClaim. The CLI is the only
    path to state transitions. A claim proposing itself as 'verified_manual'
    would still land in PROPOSED status when filed via `lawvm claim propose`.

    In this test: even if we craft a claim with requested_profiles that include
    STRICT_WITH_ATTESTED_CLAIMS, the ClaimState starts as PROPOSED until
    a CLI `accept` transitions it.
    """
    claim = _make_claim()

    # The claim can REQUEST strict profile
    assert ProfileTag.STRICT_WITH_ATTESTED_CLAIMS in claim.requested_profiles

    # But the initial state from a propose event is always PROPOSED
    propose_event = _make_event(claim.claim_id, "proposed", None, "proposed")
    state = project_state(claim.claim_id, [propose_event])

    assert state is not None
    assert state.claim_state_status == ClaimStatus.PROPOSED
    assert state.review_status == ReviewStatus.PROPOSED

    # The claim_id itself does not encode review status
    # (it's not even a field of ManualCompilationClaim)
    assert not hasattr(claim, "review_status")
    assert not hasattr(claim, "replay_authorized")
    assert not hasattr(claim, "admissibility_profile")


# ---------------------------------------------------------------------------
# Test: enums are complete
# ---------------------------------------------------------------------------


def test_enum_values_round_trip():
    """Enum .value round-trips through string."""
    for enum_cls in (
        ClaimLayer, SourceWitnessType, ReviewStatus, ValidatorStatus,
        ClaimStatus, ClaimConfidence, ProfileTag,
    ):
        for member in enum_cls:
            assert enum_cls(member.value) == member


# ---------------------------------------------------------------------------
# Test: frozen dataclass immutability
# ---------------------------------------------------------------------------


def test_claim_is_frozen():
    """ManualCompilationClaim is frozen — attribute mutation raises."""
    claim = _make_claim()
    with pytest.raises((AttributeError, TypeError)):
        cast(Any, claim).rationale = "mutated"


def test_state_is_frozen():
    """ClaimState is frozen."""
    state = ClaimState(
        claim_id="abc",
        claim_state_status=ClaimStatus.PROPOSED,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.UNVALIDATED,
        confidence=ClaimConfidence.MEDIUM,
        last_updated=datetime(2026, 6, 4, tzinfo=timezone.utc),
    )
    with pytest.raises((AttributeError, TypeError)):
        cast(Any, state).claim_state_status = ClaimStatus.ACCEPTED

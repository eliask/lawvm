"""Tests for Slice 5 retraction lifecycle + taint reports.

ClaimStore-direct acceptance criteria still validated here:
  10. test_consumption_event_logged_per_consumed_claim
  14. test_strict_rebuild_refuses_retracted_claim (strict build properly
      observes retraction — retracted claims leave NULL slots as NULL)

Criteria 11/12/13/15 exercised the v2.2 ClaimStore-backed `lawvm claim
retract` / `taint-report` CLI. Commit 7d0eb1df migrated those CLI commands
to the v3 GraphStore / ProvenanceAssertion substrate (they no longer read
ClaimStore), so those four tests — which seeded a ClaimStore and then drove
the migrated CLI — became incoherent and are now covered against the current
substrate by tests/test_fi_cmd_claim_v3.py
(test_cmd_claim_retract_emits_retracted_and_renders_taint,
test_cmd_claim_taint_report_computed_at_query_time, and the claim_id
variants). They were deleted rather than ported to avoid duplicating the v3
suite. The build_id/affected_builds/invalidated_PIT_intervals taint-report
data shape they asserted no longer exists in the GraphStore taint model.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

importlib.import_module("lawvm.finland.claim_kinds")

from lawvm.core.manual_claims.hashing import compute_claim_id
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
from lawvm.core.manual_claims.storage import ClaimStore


def _make_producer() -> Producer:
    return Producer(
        producer_kind="operator",
        handle="test",
        model_id=None,
        timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        environment="test",
    )


def _make_args(**kwargs):
    class _Args:
        pass
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def _make_and_accept_claim(
    store: ClaimStore,
    statute_id: str = "711/2022",
    citation_form: str = "lain 1234/2020",
    resolved_statute_id: str = "1234/2020",
) -> str:
    """Create, file, and accept an INLINE_STATUTE_RESOLUTION claim. Returns claim_id."""
    source_bytes = citation_form.encode()
    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version="v1",
        jurisdiction="fi",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id=statute_id,
            provision_ref="section:3",
            valid_at_start=date(2020, 1, 1),
            valid_at_end=None,
        ),
        target=(
            ("statute_id", statute_id),
            ("section_locator", "section:3"),
            ("mention_span", (0, len(source_bytes))),
        ),
        value=(
            ("resolved_statute_id", resolved_statute_id),
            ("citation_form", citation_form),
        ),
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_make_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=statute_id,
            he_id=None,
            version_id=None,
        ),
        cited_source_span=(0, len(source_bytes)),
        cited_source_hash=hashlib.sha256(source_bytes).hexdigest(),
        dependency_fingerprint=(("target_hash", "abc"),),
        valid_at=(date(2020, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale="lifecycle test claim",
    )
    claim_id = compute_claim_id(partial)
    claim = ManualCompilationClaim(
        claim_id=claim_id,
        **{k: getattr(partial, k) for k in partial.__dataclass_fields__ if k != "claim_id"},
    )

    store.ensure_dirs()
    store.write_claim(claim)
    store.write_by_kind(claim)
    now = datetime.now(tz=timezone.utc)
    producer = _make_producer()

    store.append_event(ClaimStateEvent(
        claim_id=claim_id, event_kind="proposed", timestamp=now,
        producer=producer, old_status=None, new_status="proposed", reason="test",
    ))
    store.append_event(ClaimStateEvent(
        claim_id=claim_id, event_kind="accepted", timestamp=now,
        producer=producer, old_status="proposed", new_status="accepted", reason="test accept",
    ))
    store.write_state(ClaimState(
        claim_id=claim_id,
        claim_state_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
        confidence=ClaimConfidence.HIGH,
        last_updated=now,
    ))
    return claim_id


# ---------------------------------------------------------------------------
# Test 10: consumption event logged per consumed claim
# ---------------------------------------------------------------------------


def test_consumption_event_logged_per_consumed_claim(tmp_path: Path):
    """After export_fi_refs consumes a claim, event log contains a 'consumed' event."""
    from lawvm.tools.export_fi_refs import track_consumption_for_build

    claims_dir = tmp_path / "manual_claims"
    store = ClaimStore(claims_dir)
    claim_id = _make_and_accept_claim(store)

    track_consumption_for_build(
        build_id="build-test-001",
        profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        projection_artifact_path="/data/fi_refs__strict_with_attested_claims.parquet",
        consumed_claim_ids=[claim_id],
        affected_projection_rows=[{"target_statute_id": "1234/2020", "source_statute_id": "711/2022"}],
        claims_base_dir=claims_dir,
    )

    events = list(store.read_events(claim_id))
    consumed_events = [e for e in events if e.event_kind == "consumed"]
    assert len(consumed_events) == 1

    payload = json.loads(consumed_events[0].reason)
    assert payload["build_id"] == "build-test-001"
    assert payload["profile"] == "strict_with_attested_claims"


# ---------------------------------------------------------------------------
# Test 14: strict rebuild observes retracted claim (slot remains NULL)
# ---------------------------------------------------------------------------


def test_strict_rebuild_refuses_retracted_claim(tmp_path: Path):
    """After retraction, export_fi_refs in strict mode does NOT use the retracted claim.

    Spec §5.4: new builds at strict profile refuse to incorporate retracted claims.
    Since the retracted claim is no longer in ClaimStatus.ACCEPTED,
    _load_accepted_inline_statute_claims won't load it, and the NULL slot stays NULL.

    This test verifies _check_no_retracted_claims_in_strict raises for explicitly
    constructed retracted state.
    """
    from lawvm.tools.export_fi_refs import _check_no_retracted_claims_in_strict
    from lawvm.core.manual_claims.primitive import ClaimStatus

    # Build a (claim, state) pair where state.claim_state_status = RETRACTED
    # (simulates what _load_accepted_inline_statute_claims might return if
    # the filter was removed — we test the guard function directly)
    store = ClaimStore(tmp_path / "manual_claims")
    claim_id = _make_and_accept_claim(store)

    # Manually transition to retracted state
    now = datetime.now(tz=timezone.utc)
    producer = _make_producer()
    store.append_event(ClaimStateEvent(
        claim_id=claim_id,
        event_kind="retracted",
        timestamp=now,
        producer=producer,
        old_status="accepted",
        new_status="retracted",
        reason="test retraction",
    ))
    store.write_state(ClaimState(
        claim_id=claim_id,
        claim_state_status=ClaimStatus.RETRACTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
        confidence=ClaimConfidence.HIGH,
        last_updated=now,
    ))

    claim = store.read_claim(claim_id)
    state = store.read_state(claim_id)

    # _check_no_retracted_claims_in_strict should raise SystemExit for strict profile
    with pytest.raises(SystemExit):
        _check_no_retracted_claims_in_strict(
            [(claim, state)],
            ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        )

    # For non-strict profile, no error
    _check_no_retracted_claims_in_strict(
        [(claim, state)],
        ProfileTag.NON_STRICT_WITH_CLAIMS,
    )  # must not raise


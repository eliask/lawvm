"""Tests for manual claims storage layer.

Covers storage round-trip, event log append-only invariant, state
materialization, and by-kind convenience views.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

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


# Re-use helper from primitive tests
def _make_producer() -> Producer:
    return Producer(
        producer_kind="operator",
        handle="test",
        model_id=None,
        timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        environment="test",
    )


def _make_claim(statute_id: str = "711/2022") -> ManualCompilationClaim:
    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version="v1",
        jurisdiction="fi",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id=statute_id,
            provision_ref="section:3",
            valid_at_start=date(2022, 1, 1),
            valid_at_end=None,
        ),
        target=(("statute_id", statute_id), ("mention_span", (100, 120))),
        value=(("resolved_statute_id", "1234/2020"), ("citation_form", "lain 1234/2020")),
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_make_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=statute_id,
            he_id=None,
            version_id=None,
        ),
        cited_source_span=(100, 120),
        cited_source_hash="a" * 64,
        dependency_fingerprint=(("target_hash", "abc123"),),
        valid_at=(date(2022, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale=f"test claim for {statute_id}",
    )
    claim_id = compute_claim_id(partial)
    return ManualCompilationClaim(
        claim_id=claim_id,
        schema_version="v1",
        jurisdiction="fi",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id=statute_id,
            provision_ref="section:3",
            valid_at_start=date(2022, 1, 1),
            valid_at_end=None,
        ),
        target=(("statute_id", statute_id), ("mention_span", (100, 120))),
        value=(("resolved_statute_id", "1234/2020"), ("citation_form", "lain 1234/2020")),
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_make_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=statute_id,
            he_id=None,
            version_id=None,
        ),
        cited_source_span=(100, 120),
        cited_source_hash="a" * 64,
        dependency_fingerprint=(("target_hash", "abc123"),),
        valid_at=(date(2022, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale=f"test claim for {statute_id}",
    )


def _make_state(claim_id: str, status: ClaimStatus = ClaimStatus.PROPOSED) -> ClaimState:
    return ClaimState(
        claim_id=claim_id,
        claim_state_status=status,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.UNVALIDATED,
        confidence=ClaimConfidence.MEDIUM,
        last_updated=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_event(claim_id: str, kind: str = "proposed", new_status: str = "proposed") -> ClaimStateEvent:
    return ClaimStateEvent(
        claim_id=claim_id,
        event_kind=kind,
        timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        producer=_make_producer(),
        old_status=None,
        new_status=new_status,
        reason="test",
    )


class TestClaimStore:
    def test_write_and_read_claim_roundtrip(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        claim = _make_claim()
        store.write_claim(claim)
        loaded = store.read_claim(claim.claim_id)
        assert loaded.claim_id == claim.claim_id
        assert loaded.claim_kind == claim.claim_kind
        assert loaded.jurisdiction == claim.jurisdiction
        assert loaded.rationale == claim.rationale

    def test_read_claim_verifies_hash(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        claim = _make_claim()
        store.write_claim(claim)
        path = store._objects_dir / f"{claim.claim_id}.json"
        raw = json.loads(path.read_text())
        raw["rationale"] = "tampered"
        path.write_text(json.dumps(raw))
        with pytest.raises(ValueError, match="Claim ID mismatch"):
            store.read_claim(claim.claim_id)

    def test_write_claim_is_idempotent(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        claim = _make_claim()
        p1 = store.write_claim(claim)
        p2 = store.write_claim(claim)
        assert p1 == p2
        assert len(list(store._objects_dir.iterdir())) == 1

    def test_claim_missing_raises_file_not_found(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        with pytest.raises(FileNotFoundError):
            store.read_claim("nonexistent" * 4)

    def test_write_and_read_state(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        claim = _make_claim()
        state = _make_state(claim.claim_id)
        store.write_state(state)
        loaded = store.read_state(claim.claim_id)
        assert loaded is not None
        assert loaded.claim_id == claim.claim_id
        assert loaded.claim_state_status == ClaimStatus.PROPOSED

    def test_state_read_returns_none_when_absent(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        assert store.read_state("nonexistent") is None

    def test_events_append_only(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        claim = _make_claim()
        e1 = _make_event(claim.claim_id, "proposed", "proposed")
        e2 = _make_event(claim.claim_id, "accepted", "accepted")

        store.append_event(e1)
        content_after_e1 = store._events_path.read_text()

        store.append_event(e2)
        content_after_e2 = store._events_path.read_text()

        # Original content is a prefix of updated content
        assert content_after_e2.startswith(content_after_e1)

        events = list(store.read_events(claim.claim_id))
        assert len(events) == 2
        assert events[0].event_kind == "proposed"
        assert events[1].event_kind == "accepted"

    def test_list_all_claim_ids(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        c1 = _make_claim("711/2022")
        c2 = _make_claim("712/2022")
        store.write_claim(c1)
        store.write_claim(c2)
        ids = store.list_all_claim_ids()
        assert c1.claim_id in ids
        assert c2.claim_id in ids

    def test_by_kind_view(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        claim = _make_claim()
        store.write_claim(claim)
        store.write_by_kind(claim)
        ids = store.list_claims_by_kind("fi.v1.INLINE_STATUTE_RESOLUTION")
        assert claim.claim_id in ids

    def test_list_state_ids(self, tmp_path: Path):
        store = ClaimStore(tmp_path / "mc")
        claim = _make_claim()
        state = _make_state(claim.claim_id)
        store.write_state(state)
        ids = store.list_state_ids()
        assert claim.claim_id in ids

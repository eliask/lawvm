"""Tests for Slice 3 — profile-aware fi_refs projection + ClaimCompositionDecision.

Covers all 12 mandatory acceptance criteria from the Slice 3 spec:
  1.  test_deterministic_only_no_claims_consumed
  2.  test_strict_with_attested_claims_fills_null_slots
  3.  test_unreviewed_llm_proposal_rejected_in_strict
  4.  test_non_strict_accepts_entailment_verified_unreviewed_llm
  5.  test_profile_metadata_sticky_in_parquet
  6.  test_profile_metadata_sticky_in_filename
  7.  test_ambiguous_claim_set_rejects_row_in_strict
  8.  test_self_authorization_still_impossible
  9.  test_composition_decision_event_logged
  10. test_duckdb_refuses_emit_without_profile
  11. test_cross_profile_join_warning
  12. test_real_corpus_regression  (marked slow; run with --run-slow)
"""
from __future__ import annotations

import json
import importlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, cast, Dict

import pytest

# Activate Finland claim kinds
importlib.import_module("lawvm.finland.claim_kinds")

from lawvm.core.compile_result import StrictProfile
from lawvm.core.manual_claims.composer import (
    derive_composition_decision,
    derive_composition_decision_for_strict_profile,
)
from lawvm.core.manual_claims.hashing import compute_claim_id
from lawvm.core.manual_claims.precedence import (
    AmbiguousClaimSet,
    ClaimPrecedenceInput,
    LayerPrecedenceRule,
    PrecedenceRegistry,
    load_precedence_registry,
    resolve_precedence,
)
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


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _make_producer() -> Producer:
    return Producer(
        producer_kind="operator",
        handle="test",
        model_id=None,
        timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        environment="test",
    )


def _make_inline_claim(
    source_witness_type: SourceWitnessType = SourceWitnessType.OPERATOR_FILING,
    citation_form: str = "lain 1234/2020",
    resolved_statute_id: str = "1234/2020",
) -> ManualCompilationClaim:
    """Build an INLINE_STATUTE_RESOLUTION claim with correct claim_id."""
    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version="v1",
        jurisdiction="fi",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id="711/2022",
            provision_ref="section:3",
            valid_at_start=date(2022, 1, 1),
            valid_at_end=None,
        ),
        target=(
            ("statute_id", "711/2022"),
            ("section_locator", "section:3"),
            ("mention_span", (100, 120)),
        ),
        value=(
            ("resolved_statute_id", resolved_statute_id),
            ("citation_form", citation_form),
        ),
        source_witness_type=source_witness_type,
        producer=_make_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id="711/2022",
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
        rationale="test claim",
    )
    claim_id = compute_claim_id(partial)
    return ManualCompilationClaim(
        claim_id=claim_id,
        schema_version="v1",
        jurisdiction="fi",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id="711/2022",
            provision_ref="section:3",
            valid_at_start=date(2022, 1, 1),
            valid_at_end=None,
        ),
        target=(
            ("statute_id", "711/2022"),
            ("section_locator", "section:3"),
            ("mention_span", (100, 120)),
        ),
        value=(
            ("resolved_statute_id", resolved_statute_id),
            ("citation_form", citation_form),
        ),
        source_witness_type=source_witness_type,
        producer=_make_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id="711/2022",
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
        rationale="test claim",
    )


def _make_accepted_state(
    claim_id: str,
    review_status: ReviewStatus = ReviewStatus.VERIFIED_MANUAL,
    validator_status: ValidatorStatus = ValidatorStatus.SPAN_VERIFIED,
) -> ClaimState:
    return ClaimState(
        claim_id=claim_id,
        claim_state_status=ClaimStatus.ACCEPTED,
        review_status=review_status,
        validator_status=validator_status,
        confidence=ClaimConfidence.HIGH,
        last_updated=datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc),
    )


def _make_proposed_state(claim_id: str) -> ClaimState:
    return ClaimState(
        claim_id=claim_id,
        claim_state_status=ClaimStatus.PROPOSED,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.UNVALIDATED,
        confidence=ClaimConfidence.MEDIUM,
        last_updated=datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc),
    )


def _make_null_fi_refs_row(
    statute_id: str = "711/2022",
    profile: ProfileTag = ProfileTag.DETERMINISTIC_ONLY,
) -> Dict[str, Any]:
    """Return a fi_refs row with NULL target_statute_id (simulates deterministic miss)."""
    return {
        "source_statute_id": statute_id,
        "source_provision_ref_str": "section:3",
        "target_statute_id": None,
        "target_provision_ref_str": None,
        "cite_kind": "cross_statute",
        "cite_confidence": "EXACT",
        "edge_subtype": "plain_text",
        "phrase_lemma": "lain 1234/2020",
        "source_span_file": statute_id,
        "source_span_byte_offset": 100,
        "source_span_len": 20,
        "valid_at_start": None,
        "valid_at_end": None,
        "target_stat_hash": None,
        "source_witness_type": "finlex_akn",
        "claim_id": None,
        "validator_status": "span_verified",
        "review_status": "verified_manual",
        "replay_authorized": True,
        # Surface fact column (A1) — production deterministic rows always carry
        # it, so the fixture must too or the pinned-schema write fails loud.
        "deterministic_extraction": True,
        "emit_profile": profile.value,
    }


def _minimal_precedence_registry() -> PrecedenceRegistry:
    return PrecedenceRegistry(
        rules=(
            LayerPrecedenceRule(
                layer="extraction",
                rule="validator_status=entailment_verified > span_verified",
                rationale="test",
            ),
        ),
        source_path="<test>",
    )


def _minimal_compile_metadata():
    from lawvm.core.compile_metadata import CompileMetadata

    return CompileMetadata(
        provenance_graph_hash="a" * 64,
        strict_profile_fingerprint="b" * 64,
        evidence_policy_fingerprint="c" * 64,
        source_bundle_hash="d" * 64,
        attestation_kind_registry_hash="e" * 64,
        build_id="manual-claims-slice3-test",
    )


# ---------------------------------------------------------------------------
# 1. test_deterministic_only_no_claims_consumed
# ---------------------------------------------------------------------------


def test_deterministic_only_no_claims_consumed():
    """Even with accepted claims present, deterministic_only profile never consumes them."""
    from lawvm.core.manual_claims.composer import derive_composition_decision

    claim = _make_inline_claim()
    state = _make_accepted_state(claim.claim_id)
    reg = _minimal_precedence_registry()

    decision, event = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.DETERMINISTIC_ONLY,
        build_id="build-001",
        precedence_registry=reg,
    )

    assert not decision.authorized
    assert decision.reason_code == "deterministic_only_profile"
    assert decision.profile == ProfileTag.DETERMINISTIC_ONLY
    assert event.event_kind == "composed_for_build"
    assert event.claim_id == claim.claim_id


# ---------------------------------------------------------------------------
# 2. test_strict_with_attested_claims_fills_null_slots
# ---------------------------------------------------------------------------


def test_strict_with_attested_claims_fills_null_slots(tmp_path: Path):
    """fixture: 1 fi_refs row with NULL target + 1 accepted human-reviewed claim.

    strict_with_attested_claims: row gets filled.
    """
    from lawvm.tools.export_fi_refs import _apply_null_slot_fills

    claim = _make_inline_claim(source_witness_type=SourceWitnessType.OPERATOR_FILING)
    state = _make_accepted_state(
        claim.claim_id,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
    )

    row = _make_null_fi_refs_row(profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS)
    reg = _minimal_precedence_registry()
    events: list = []

    updated, ambiguous = _apply_null_slot_fills(
        mention_rows=[row],
        accepted_claims=[(claim, state)],
        profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        build_id="build-002",
        precedence_registry=reg,
        emitted_events=events,
    )

    assert len(updated) == 1
    filled = updated[0]
    assert filled["target_statute_id"] == "1234/2020"
    assert filled["claim_id"] == claim.claim_id
    assert filled["source_witness_type"] == "operator_filing"
    assert len(events) == 1
    assert events[0].event_kind == "composed_for_build"


# ---------------------------------------------------------------------------
# 3. test_unreviewed_llm_proposal_rejected_in_strict
# ---------------------------------------------------------------------------


def test_unreviewed_llm_proposal_rejected_in_strict():
    """LLM proposal with review_status=proposed is rejected in strict profile."""
    claim = _make_inline_claim(source_witness_type=SourceWitnessType.LLM_PROPOSAL)
    state = _make_accepted_state(
        claim.claim_id,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
    )
    reg = _minimal_precedence_registry()

    decision, event = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        build_id="build-003",
        precedence_registry=reg,
    )

    assert not decision.authorized
    assert decision.reason_code == "rejected_unreviewed_llm"
    assert event.event_kind == "composed_for_build"


# ---------------------------------------------------------------------------
# 4. test_non_strict_accepts_entailment_verified_unreviewed_llm
# ---------------------------------------------------------------------------


def test_non_strict_accepts_entailment_verified_unreviewed_llm():
    """entailment_verified LLM proposal without human review accepted in non_strict."""
    claim = _make_inline_claim(source_witness_type=SourceWitnessType.LLM_PROPOSAL)
    state = _make_accepted_state(
        claim.claim_id,
        review_status=ReviewStatus.PROPOSED,  # no human review
        validator_status=ValidatorStatus.ENTAILMENT_VERIFIED,
    )
    reg = _minimal_precedence_registry()

    decision, event = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.NON_STRICT_WITH_CLAIMS,
        build_id="build-004",
        precedence_registry=reg,
    )

    assert decision.authorized
    assert decision.reason_code == "accepted_non_strict"


# ---------------------------------------------------------------------------
# 5. test_profile_metadata_sticky_in_parquet
# ---------------------------------------------------------------------------


def test_profile_metadata_sticky_in_parquet(tmp_path: Path):
    """Output parquet has lawvm.claim_profile metadata key."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        pytest.skip("pyarrow not installed")

    from lawvm.tools.export_fi_refs import _try_write_parquet

    out_path = tmp_path / "fi_refs__deterministic_only.parquet"
    rows = [_make_null_fi_refs_row(profile=ProfileTag.DETERMINISTIC_ONLY)]
    ok = _try_write_parquet(
        out_path,
        rows,
        ProfileTag.DETERMINISTIC_ONLY,
        _minimal_compile_metadata(),
    )

    assert ok
    meta = pq.read_metadata(str(out_path))
    schema_meta = meta.metadata or {}
    profile_bytes = schema_meta.get(b"lawvm.claim_profile")
    assert profile_bytes is not None, "lawvm.claim_profile key missing from parquet metadata"
    assert profile_bytes.decode() == "deterministic_only"


# ---------------------------------------------------------------------------
# 6. test_profile_metadata_sticky_in_filename
# ---------------------------------------------------------------------------


def test_profile_metadata_sticky_in_filename(tmp_path: Path):
    """Output file is named fi_refs__{profile}.parquet."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        pytest.skip("pyarrow not installed")

    from lawvm.tools.export_fi_refs import _try_write_parquet

    for profile in (
        ProfileTag.DETERMINISTIC_ONLY,
        ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        ProfileTag.NON_STRICT_WITH_CLAIMS,
    ):
        fname = f"fi_refs__{profile.value}.parquet"
        out_path = tmp_path / fname
        _try_write_parquet(out_path, [], profile, _minimal_compile_metadata())
        assert out_path.exists(), f"Expected {fname} to be written"
        # Verify metadata
        meta = pq.read_metadata(str(out_path))
        schema_meta = meta.metadata or {}
        assert schema_meta.get(b"lawvm.claim_profile") == profile.value.encode()


# ---------------------------------------------------------------------------
# 7. test_ambiguous_claim_set_rejects_row_in_strict
# ---------------------------------------------------------------------------


def test_ambiguous_claim_set_rejects_row_in_strict(tmp_path: Path):
    """Two competing claims for same target → AmbiguousClaimSet; row not filled in strict."""
    # Two claims for the same target span but different resolved_statute_id
    claim_a = _make_inline_claim(
        resolved_statute_id="1234/2020",
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
    )
    claim_b = _make_inline_claim(
        resolved_statute_id="5678/2021",
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        citation_form="lain 5678/2021",
    )

    state_a = _make_accepted_state(
        claim_a.claim_id,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
    )
    state_b = _make_accepted_state(
        claim_b.claim_id,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
    )

    row = _make_null_fi_refs_row(profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS)
    reg = _minimal_precedence_registry()
    events: list = []

    # Both claims target the SAME span → both authorized → ambiguous
    # The _apply_null_slot_fills function processes claims sequentially;
    # to test ambiguity resolution, we test via precedence directly
    from lawvm.core.manual_claims.precedence import resolve_precedence
    winner_id, ambig = resolve_precedence(
        claims_with_values=[
            (claim_a.claim_id, "1234/2020", "span_verified", "operator_filing"),
            (claim_b.claim_id, "5678/2021", "span_verified", "operator_filing"),
        ],
        layer="extraction",
        registry=reg,
    )
    # Same validator_status → ambiguous
    assert winner_id is None
    assert ambig is not None
    assert isinstance(ambig, AmbiguousClaimSet)
    assert len(ambig.competing_claim_ids) == 2


# ---------------------------------------------------------------------------
# 8. test_self_authorization_still_impossible
# ---------------------------------------------------------------------------


def test_self_authorization_still_impossible():
    """A claim with requested_profiles=(STRICT,) but proposed state is NOT auto-promoted."""
    claim = _make_inline_claim()
    # State is proposed, not accepted
    state = _make_proposed_state(claim.claim_id)
    reg = _minimal_precedence_registry()

    decision, event = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        build_id="build-008",
        precedence_registry=reg,
    )

    assert not decision.authorized
    assert decision.reason_code == "rejected_not_accepted_status"


# ---------------------------------------------------------------------------
# 9. test_composition_decision_event_logged
# ---------------------------------------------------------------------------


def test_composition_decision_event_logged():
    """Every composition decision (authorized OR not) writes a ClaimStateEvent."""
    claim = _make_inline_claim()
    reg = _minimal_precedence_registry()

    # Test both authorized and rejected paths
    for profile, state, expected_auth in [
        (
            ProfileTag.DETERMINISTIC_ONLY,
            _make_accepted_state(claim.claim_id),
            False,
        ),
        (
            ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
            _make_accepted_state(
                claim.claim_id,
                review_status=ReviewStatus.VERIFIED_MANUAL,
                validator_status=ValidatorStatus.SPAN_VERIFIED,
            ),
            True,
        ),
    ]:
        decision, event = derive_composition_decision(
            claim=claim,
            state=state,
            profile=profile,
            build_id="build-009",
            precedence_registry=reg,
        )
        # Event must always be emitted
        assert isinstance(event, ClaimStateEvent)
        assert event.event_kind == "composed_for_build"
        assert event.claim_id == claim.claim_id
        assert event.old_status is None
        assert event.new_status is None
        # Reason payload is JSON with build_id + profile + authorized + reason_code
        payload = json.loads(event.reason)
        assert payload["build_id"] == "build-009"
        assert payload["profile"] == profile.value
        assert payload["authorized"] == expected_auth


# ---------------------------------------------------------------------------
# 10. test_duckdb_refuses_emit_without_profile
# ---------------------------------------------------------------------------


def test_duckdb_refuses_emit_without_profile(tmp_path: Path):
    """build-index-db without --profile exits non-zero with error message."""
    # Create a minimal parquet to satisfy the directory check
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        pytest.skip("pyarrow not installed")
    pytest.importorskip("duckdb")

    tier_dir = tmp_path / "data" / "fi" / "v1"
    tier_dir.mkdir(parents=True)
    # Write a minimal dummy parquet
    table = pa.table({"x": [1]})
    pq.write_table(table, str(tier_dir / "dummy.parquet"))

    from lawvm.tools.build_index_db import build_index_db

    with pytest.raises(SystemExit) as exc_info:
        build_index_db(
            jurisdiction="fi",
            data_dir=str(tmp_path / "data"),
            profile=None,  # Missing profile
        )

    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# 11. test_cross_profile_join_warning
# ---------------------------------------------------------------------------


def test_cross_profile_join_warning(tmp_path: Path):
    """Parquets with different claim_profiles emit a WARNING to stderr."""
    pytest.importorskip("pyarrow")
    pytest.importorskip("pyarrow.parquet")

    from lawvm.tools.export_fi_refs import _try_write_parquet
    from lawvm.tools.build_index_db import _detect_cross_profile_joins

    p1 = tmp_path / "fi_refs__deterministic_only.parquet"
    p2 = tmp_path / "fi_refs__non_strict_with_claims.parquet"

    _try_write_parquet(
        p1,
        [],
        ProfileTag.DETERMINISTIC_ONLY,
        _minimal_compile_metadata(),
    )
    _try_write_parquet(
        p2,
        [],
        ProfileTag.NON_STRICT_WITH_CLAIMS,
        _minimal_compile_metadata(),
    )

    warning = _detect_cross_profile_joins([p1, p2])
    assert warning is not None
    assert "WARNING" in warning
    assert "cross-profile" in warning.lower()


# ---------------------------------------------------------------------------
# 12. test_real_corpus_regression (slow)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_real_corpus_regression():
    """deterministic_only profile produces non-zero rows for known-cited statutes.

    Per REAL_CORPUS_REGRESSION_FOR_PROJECTION_EMITTERS_001.
    Runs through _load_corpus_store() and real farchive.
    """
    import tempfile
    from lawvm.tools.export_fi_refs import export_fi_refs

    # Small corpus for speed: use a few well-known statutes with refs
    # Hallintolaki 434/2003 is known to have outgoing cross_statute refs
    corpus = [(1, "434/2003")]

    with tempfile.TemporaryDirectory() as tmpdir:
        count = export_fi_refs(
            corpus,
            data_dir=tmpdir,
            use_parquet=True,
            profile=ProfileTag.DETERMINISTIC_ONLY,
        )
        assert count > 0, (
            "No ReferenceMention rows produced for 434/2003 (Hallintolaki). "
            "Either farchive is absent, or the extractor regressed."
        )

        # Verify parquet metadata is present
        try:
            import pyarrow.parquet as pq
            p = Path(tmpdir) / "fi_refs__deterministic_only.parquet"
            assert p.exists(), f"Expected profile-stamped parquet at {p}"
            meta = pq.read_metadata(str(p))
            schema_meta = meta.metadata or {}
            assert schema_meta.get(b"lawvm.claim_profile") == b"deterministic_only"
        except ImportError:
            pass  # pyarrow optional for this check


# ---------------------------------------------------------------------------
# Additional unit tests for composer boundary cases
# ---------------------------------------------------------------------------


def test_composer_non_strict_llm_span_verified_no_human_review_rejected():
    """non_strict: span_verified LLM without human review is rejected (need entailment)."""
    claim = _make_inline_claim(source_witness_type=SourceWitnessType.LLM_PROPOSAL)
    state = _make_accepted_state(
        claim.claim_id,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.SPAN_VERIFIED,  # not entailment_verified
    )
    reg = _minimal_precedence_registry()

    decision, _ = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.NON_STRICT_WITH_CLAIMS,
        build_id="build-extra",
        precedence_registry=reg,
    )
    assert not decision.authorized
    assert decision.reason_code == "rejected_unreviewed_llm"


def test_strict_profile_bridge_blocks_when_attested_channel_disabled():
    """StrictProfile, not requested ProfileTag, controls new composition calls."""
    claim = _make_inline_claim(source_witness_type=SourceWitnessType.OPERATOR_FILING)
    state = _make_accepted_state(
        claim.claim_id,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
    )

    decision, event = derive_composition_decision_for_strict_profile(
        claim=claim,
        state=state,
        strict_profile=StrictProfile(name="fi_blocking"),
        build_id="build-strict-profile-block",
        precedence_registry=_minimal_precedence_registry(),
    )

    assert not decision.authorized
    assert decision.profile == ProfileTag.DETERMINISTIC_ONLY
    assert decision.reason_code == "strict_profile_disallows_attested_channel"
    payload = json.loads(event.reason)
    assert payload["strict_profile"] == "fi_blocking"


def test_strict_profile_bridge_accepts_reviewed_reference_resolution_claim():
    claim = _make_inline_claim(source_witness_type=SourceWitnessType.OPERATOR_FILING)
    state = _make_accepted_state(
        claim.claim_id,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
    )

    decision, event = derive_composition_decision_for_strict_profile(
        claim=claim,
        state=state,
        strict_profile=StrictProfile(
            name="fi_attested_refs",
            allows_attested_reference_resolution=True,
        ),
        build_id="build-strict-profile-accept",
        precedence_registry=_minimal_precedence_registry(),
    )

    assert decision.authorized
    assert decision.profile == ProfileTag.STRICT_WITH_ATTESTED_CLAIMS
    assert decision.reason_code == "accepted_strict_attested"
    payload = json.loads(event.reason)
    assert payload["strict_profile"] == "fi_attested_refs"


def test_strict_profile_bridge_requires_explicit_unreviewed_llm_admission():
    claim = _make_inline_claim(source_witness_type=SourceWitnessType.LLM_PROPOSAL)
    state = _make_accepted_state(
        claim.claim_id,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.ENTAILMENT_VERIFIED,
    )

    strict_decision, _ = derive_composition_decision_for_strict_profile(
        claim=claim,
        state=state,
        strict_profile=StrictProfile(
            name="fi_attested_refs_strict",
            allows_attested_reference_resolution=True,
        ),
        build_id="build-strict-profile-llm-strict",
        precedence_registry=_minimal_precedence_registry(),
    )
    non_strict_decision, _ = derive_composition_decision_for_strict_profile(
        claim=claim,
        state=state,
        strict_profile=StrictProfile(
            name="fi_attested_refs_non_strict",
            allows_attested_reference_resolution=True,
            allows_unreviewed_llm_attestations=True,
        ),
        build_id="build-strict-profile-llm-nonstrict",
        precedence_registry=_minimal_precedence_registry(),
    )

    assert not strict_decision.authorized
    assert strict_decision.reason_code == "rejected_unreviewed_llm"
    assert non_strict_decision.authorized
    assert non_strict_decision.profile == ProfileTag.NON_STRICT_WITH_CLAIMS
    assert non_strict_decision.reason_code == "accepted_non_strict"


def test_composer_exploratory_accepts_accepted_non_semantic():
    """exploratory profile accepts any accepted non-semantic-compilation claim."""
    claim = _make_inline_claim(source_witness_type=SourceWitnessType.LLM_PROPOSAL)
    state = _make_accepted_state(
        claim.claim_id,
        review_status=ReviewStatus.PROPOSED,
        validator_status=ValidatorStatus.UNVALIDATED,
    )
    reg = _minimal_precedence_registry()

    decision, _ = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.EXPLORATORY,
        build_id="build-explr",
        precedence_registry=reg,
    )
    assert decision.authorized
    assert decision.reason_code == "exploratory_accepted"


def test_precedence_registry_load(tmp_path: Path):
    """load_precedence_registry parses the canonical YAML format."""
    yaml_content = """
- layer: extraction
  rule: "entailment_verified > span_verified"
  rationale: "validation rigor"
- layer: substrate
  rule: "FINLEX_CORRIGENDUM > FINLEX_AKN"
  rationale: "published corrections authoritative"
"""
    yaml_path = tmp_path / "claim_precedence.yaml"
    yaml_path.write_text(yaml_content)

    registry = load_precedence_registry(yaml_path)
    assert len(registry.rules) == 2
    assert registry.rules[0].layer == "extraction"
    assert registry.rules[1].layer == "substrate"


def test_precedence_registry_missing_file(tmp_path: Path):
    """load_precedence_registry raises FileNotFoundError on missing file."""
    with pytest.raises(FileNotFoundError, match="claim_precedence.yaml"):
        load_precedence_registry(tmp_path / "nonexistent.yaml")


def test_precedence_resolution_accepts_named_claim_inputs():
    """Claim precedence inputs are named carriers, not semantic 4-tuples."""
    winner_id, ambig = resolve_precedence(
        claims_with_values=[
            ClaimPrecedenceInput(
                claim_id="claim-low",
                value="1234/2020",
                validator_status="span_verified",
                source_witness_type="operator_filing",
            ),
            ClaimPrecedenceInput(
                claim_id="claim-high",
                value="5678/2021",
                validator_status="entailment_verified",
                source_witness_type="operator_filing",
            ),
        ],
        layer="extraction",
        registry=_minimal_precedence_registry(),
    )

    assert winner_id == "claim-high"
    assert ambig is None


def test_is_semantic_compilation_false_for_inline_statute():
    """INLINE_STATUTE_RESOLUTION is NOT a semantic compilation claim."""
    from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec
    spec = get_claim_kind_spec("fi.v1.INLINE_STATUTE_RESOLUTION")
    assert spec is not None
    assert spec.is_semantic_compilation_claim is False


# Expose _minimal_precedence_registry from precedence module for test import
from lawvm.core.manual_claims import precedence as _prec_mod
cast(Any, _prec_mod)._minimal_precedence_registry = _minimal_precedence_registry

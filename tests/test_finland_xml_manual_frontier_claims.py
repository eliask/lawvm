"""Tests for Finland XML-backed manual-frontier claim kinds."""
from __future__ import annotations

import hashlib
import importlib
from datetime import date, datetime, timezone

importlib.import_module("lawvm.finland.claim_kinds")

from lawvm.core.manual_claims.composer import derive_composition_decision
from lawvm.core.manual_claims.hashing import compute_claim_id
from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec, list_registered_kinds
from lawvm.core.manual_claims.precedence import PrecedenceRegistry
from lawvm.core.manual_claims.primitive import (
    ClaimConfidence,
    ClaimLayer,
    ClaimScope,
    ClaimState,
    ClaimStatus,
    ManualCompilationClaim,
    Producer,
    ProfileTag,
    ReviewStatus,
    SourceLocator,
    SourceWitnessType,
    ValidatorStatus,
)


_XML_FRONTIER_KINDS = (
    "fi.v1.CORRIGENDUM_SOURCE_CORRECTION",
    "fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
    "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
    "fi.v1.CONTAINER_MEMBERSHIP_RESOLUTION",
    "fi.v1.SOURCE_CHAIN_RESOLUTION",
    "fi.v1.TEMPORAL_BASE_SELECTION_RESOLUTION",
    "fi.v1.MUTATION_BOUNDARY_RESOLUTION",
    "fi.v1.FAILED_OPERATION_RESOLUTION",
)


def _producer() -> Producer:
    return Producer(
        producer_kind="operator",
        handle="test",
        model_id=None,
        timestamp=datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc),
        environment="test",
    )


def _claim(
    *,
    claim_kind: str,
    target: tuple[tuple[str, object], ...],
    value: tuple[tuple[str, object], ...],
    source_bytes: bytes,
    claim_layer: ClaimLayer = ClaimLayer.ADJUDICATION,
) -> ManualCompilationClaim:
    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version="v1",
        jurisdiction="fi",
        claim_kind=claim_kind,
        claim_layer=claim_layer,
        claim_scope=ClaimScope(
            statute_id="1994/1472",
            provision_ref="section:35",
            valid_at_start=date(2026, 1, 1),
            valid_at_end=None,
        ),
        target=target,
        value=value,
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id="1994/1472",
            he_id=None,
            version_id=None,
        ),
        cited_source_span=(0, len(source_bytes)),
        cited_source_hash=hashlib.sha256(source_bytes).hexdigest(),
        dependency_fingerprint=(("source_digest", hashlib.sha256(source_bytes).hexdigest()),),
        valid_at=(date(2026, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale="test XML manual frontier claim",
    )
    claim_id = compute_claim_id(partial)
    return ManualCompilationClaim(
        claim_id=claim_id,
        schema_version=partial.schema_version,
        jurisdiction=partial.jurisdiction,
        claim_kind=partial.claim_kind,
        claim_layer=partial.claim_layer,
        claim_scope=partial.claim_scope,
        target=partial.target,
        value=partial.value,
        source_witness_type=partial.source_witness_type,
        producer=partial.producer,
        cited_source_locator=partial.cited_source_locator,
        cited_source_span=partial.cited_source_span,
        cited_source_hash=partial.cited_source_hash,
        dependency_fingerprint=partial.dependency_fingerprint,
        valid_at=partial.valid_at,
        supersedes=partial.supersedes,
        supersession_delta_reason=partial.supersession_delta_reason,
        disputes=partial.disputes,
        requested_profiles=partial.requested_profiles,
        rationale=partial.rationale,
    )


def _common_target(pathology_code: str) -> tuple[tuple[str, object], ...]:
    return (
        ("source_statute", "2005/821"),
        ("affected_target", "section:35"),
        ("source_pathology_code", pathology_code),
    )


def test_finland_xml_manual_frontier_kinds_are_registered() -> None:
    registered = set(list_registered_kinds())
    for kind in _XML_FRONTIER_KINDS:
        assert kind in registered

    correction = get_claim_kind_spec("fi.v1.CORRIGENDUM_SOURCE_CORRECTION")
    assert correction is not None
    assert correction.layer == "correction"
    assert correction.is_semantic_compilation_claim is False

    semantic = get_claim_kind_spec("fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION")
    assert semantic is not None
    assert semantic.is_semantic_compilation_claim is True

    failed_op = get_claim_kind_spec("fi.v1.FAILED_OPERATION_RESOLUTION")
    assert failed_op is not None
    assert failed_op.layer == "adjudication"
    assert failed_op.is_semantic_compilation_claim is True


def test_shipped_evidence_policy_covers_xml_manual_frontier_kinds() -> None:
    import json
    from pathlib import Path

    from lawvm.core.evidence_policy import registry_from_dict

    policy_path = Path("data/fi/v1/evidence_policy/lawvm.fi.v1.evidence_policy.v0.json")
    registry = registry_from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
    for kind in _XML_FRONTIER_KINDS:
        predicate = registry.get_predicate_for_claim_kind(kind)
        assert predicate is not None
        assert predicate.claim_kind == kind


def test_corrigendum_source_correction_validates_grounded_quote() -> None:
    source = b"<p>Korjataan virheellinen ilmaisu A muotoon B.</p>"
    claim = _claim(
        claim_kind="fi.v1.CORRIGENDUM_SOURCE_CORRECTION",
        claim_layer=ClaimLayer.CORRECTION,
        target=(
            ("source_statute", "2005/821"),
            ("affected_target", "section:35"),
            ("source_locator", "finlex://2005/821#section:35"),
        ),
        value=(
            ("source_quote", "virheellinen ilmaisu A"),
            ("correction_kind", "text_correction"),
            ("original_text", "virheellinen ilmaisu A"),
            ("corrected_text", "muotoon B"),
            ("correction_witness_digest", "a" * 64),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.CORRIGENDUM_SOURCE_CORRECTION")
    assert spec is not None
    assert spec.span_validator is not None
    assert spec.entailment_validator is not None
    assert spec.span_validator(claim, source).passed is True
    assert spec.entailment_validator(claim, source).passed is True


def test_xml_manual_frontier_rejects_missing_grounding_quote() -> None:
    source = b"<p>Payload says one thing.</p>"
    claim = _claim(
        claim_kind="fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
        target=_common_target("PARTIAL_WHOLE_SECTION_PAYLOAD"),
        value=(
            ("source_quote", "not in source"),
            ("payload_boundary", "section:35/subsection:1"),
            ("retained_live_paths", ("section:35/subsection:2",)),
            ("mutation_boundary_proof_ref", "proof-1"),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION")
    assert spec is not None
    assert spec.entailment_validator is not None
    result = spec.entailment_validator(claim, source)
    assert result.passed is False
    assert result.details == "source_quote_absent"


def test_xml_manual_frontier_rejects_wrong_pathology_family() -> None:
    source = b"<p>payload quote</p>"
    claim = _claim(
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        target=_common_target("DESTRUCTIVE_SHAPE_LOSS_RISK"),
        value=(
            ("source_quote", "payload quote"),
            ("candidate_slots", ("section:35/subsection:1/item:5",)),
            ("selected_slot", "section:35/subsection:1/item:5"),
            ("old_text_precondition", "old text"),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION")
    assert spec is not None
    assert spec.entailment_validator is not None
    result = spec.entailment_validator(claim, source)
    assert result.passed is False
    assert result.details == "source_pathology_code_mismatch"


def test_failed_operation_resolution_validates_without_pathology_code() -> None:
    source = b"<p>Target section could not be applied deterministically.</p>"
    claim = _claim(
        claim_kind="fi.v1.FAILED_OPERATION_RESOLUTION",
        target=(
            ("source_statute", "2020/1"),
            ("affected_target", "chapter:4/section:5"),
            ("failure_reason_code", "no_deterministic_path"),
        ),
        value=(
            ("source_quote", "could not be applied deterministically"),
            ("resolution_kind", "manual_target_payload_boundary"),
            ("resolution_basis", "reviewed source and live target state"),
            ("mutation_boundary_proof_ref", "proof-1"),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.FAILED_OPERATION_RESOLUTION")
    assert spec is not None
    assert spec.entailment_validator is not None
    assert spec.entailment_validator(claim, source).passed is True


def test_semantic_xml_manual_frontier_claim_validates_but_composer_blocks_replay() -> None:
    source = b"<p>literal sparse slot source quote</p>"
    claim = _claim(
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        target=_common_target("SPARSE_ITEM_BODY_MISSING"),
        value=(
            ("source_quote", "sparse slot source quote"),
            ("candidate_slots", ("section:35/subsection:1/item:5",)),
            ("selected_slot", "section:35/subsection:1/item:5"),
            ("old_text_precondition", "old text"),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION")
    assert spec is not None
    assert spec.entailment_validator is not None
    assert spec.entailment_validator(claim, source).passed is True

    state = ClaimState(
        claim_id=claim.claim_id,
        status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.HUMAN_REVIEWED,
        validator_status=ValidatorStatus.ENTAILMENT_VERIFIED,
        confidence=ClaimConfidence.HIGH,
        last_updated=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    decision, _event = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        build_id="test-build",
        precedence_registry=PrecedenceRegistry(rules=(), source_path="<test>"),
    )
    assert decision.authorized is False
    assert decision.replay_authorized is False
    assert decision.reason_code == "rejected_replay_authorized_false"

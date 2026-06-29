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
from lawvm.core.phase_replay_gate import PhaseLocalReplayGate


_XML_FRONTIER_KINDS = (
    "fi.v1.CORRIGENDUM_SOURCE_CORRECTION",
    "fi.v1.PAYLOAD_COMPLETENESS_RESOLUTION",
    "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
    "fi.v1.CONTAINER_MEMBERSHIP_RESOLUTION",
    "fi.v1.SOURCE_CHAIN_RESOLUTION",
    "fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
    "fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE",
    "fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE",
    "fi.v1.TEMPORAL_BASE_SELECTION_RESOLUTION",
    "fi.v1.MUTATION_BOUNDARY_RESOLUTION",
    "fi.v1.FAILED_OPERATION_RESOLUTION",
    "fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION",
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


def _sparse_slot_proof_values() -> tuple[tuple[str, object], ...]:
    return (
        ("target_uniqueness_proof_ref", "target-proof-1"),
        ("payload_identity_proof_ref", "payload-proof-1"),
        ("rejected_candidate_accounting_ref", "rejected-candidates-proof-1"),
        ("mutation_boundary_proof_ref", "mutation-proof-1"),
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

    source_pathology = get_claim_kind_spec("fi.v1.SOURCE_PATHOLOGY_RESOLUTION")
    assert source_pathology is not None
    assert source_pathology.layer == "adjudication"
    assert source_pathology.is_semantic_compilation_claim is True

    source_unit = get_claim_kind_spec("fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE")
    assert source_unit is not None
    assert source_unit.layer == "adjudication"
    assert source_unit.is_semantic_compilation_claim is False

    operation_cue = get_claim_kind_spec("fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE")
    assert operation_cue is not None
    assert operation_cue.layer == "adjudication"
    assert operation_cue.is_semantic_compilation_claim is False

    unsupported_corrigendum = get_claim_kind_spec("fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION")
    assert unsupported_corrigendum is not None
    assert unsupported_corrigendum.layer == "adjudication"
    assert unsupported_corrigendum.is_semantic_compilation_claim is True


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
            *_sparse_slot_proof_values(),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION")
    assert spec is not None
    assert spec.entailment_validator is not None
    result = spec.entailment_validator(claim, source)
    assert result.passed is False
    assert result.details == "source_pathology_code_mismatch"


def test_sparse_slot_resolution_requires_phase_gate_proof_refs() -> None:
    source = b"<p>sparse slot source quote</p>"
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
    result = spec.entailment_validator(claim, source)

    assert result.passed is False
    assert result.details == "missing_required_fields"
    assert "target_uniqueness_proof_ref" in result.reason
    assert "payload_identity_proof_ref" in result.reason
    assert "rejected_candidate_accounting_ref" in result.reason
    assert "mutation_boundary_proof_ref" in result.reason


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


def test_source_pathology_resolution_validates_grounded_quote() -> None:
    source = b"<p>published XML has no operative body; alternative source witness reviewed</p>"
    claim = _claim(
        claim_kind="fi.v1.SOURCE_PATHOLOGY_RESOLUTION",
        target=_common_target("EMPTY_OPERATIVE_BODY"),
        value=(
            ("source_quote", "no operative body"),
            ("resolution_kind", "alternative_source_witness_required"),
            ("resolution_basis", "reviewed source pathology and bounded non-executable frontier"),
            ("mutation_boundary_proof_ref", "proof-source-pathology-1"),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.SOURCE_PATHOLOGY_RESOLUTION")
    assert spec is not None
    assert spec.entailment_validator is not None
    assert spec.entailment_validator(claim, source).passed is True


def test_source_unit_enumeration_certificate_validates_grounded_quote() -> None:
    source = b"<p>enumerated source units: 2020/1 section 1 and section 2</p>"
    claim = _claim(
        claim_kind="fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE",
        target=(
            ("source_statute", "2020/1"),
            ("affected_target", "fi:1999/1:strict-report-source-unit-enumeration"),
        ),
        value=(
            ("source_quote", "enumerated source units"),
            ("enumerated_source_units", ("2020/1#section:1", "2020/1#section:2")),
            ("coverage_basis", "declared strict-report source-unit enumeration"),
            ("digest_coverage_ref", "digest-source-units-1"),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.SOURCE_UNIT_ENUMERATION_CERTIFICATE")
    assert spec is not None
    assert spec.entailment_validator is not None
    assert spec.entailment_validator(claim, source).passed is True


def test_operation_cue_exhaustiveness_certificate_validates_grounded_quote() -> None:
    source = b"<p>operation cue detector classified all amendment cues in the span</p>"
    claim = _claim(
        claim_kind="fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE",
        target=(
            ("source_statute", "2020/1"),
            ("affected_target", "fi:1999/1:strict-report-operation-cue-coverage"),
        ),
        value=(
            ("source_quote", "classified all amendment cues"),
            ("operation_cue_detector", "fi.operation_cue_detector.v1"),
            ("classified_cues", ("replace:section:1", "insert:section:2")),
            ("coverage_basis", "declared strict-report operation-cue coverage"),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.OPERATION_CUE_EXHAUSTIVENESS_CERTIFICATE")
    assert spec is not None
    assert spec.entailment_validator is not None
    assert spec.entailment_validator(claim, source).passed is True


def test_unsupported_corrigendum_patch_resolution_validates_without_pathology_code() -> None:
    source = b"<p>Johtolauseesta puuttuu virke, joka kuuluu: lisatty teksti.</p>"
    claim = _claim(
        claim_kind="fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION",
        target=(
            ("source_statute", "corr/442/2016"),
            ("affected_target", "preamble:formula"),
            ("unsupported_reason_code", "FINLAND.CORRIGENDUM_ADD_UNSUPPORTED"),
        ),
        value=(
            ("source_quote", "puuttuu virke"),
            ("correction_kind", "ADD"),
            ("resolution_kind", "manual_corrigendum_patch_boundary"),
            ("resolution_basis", "reviewed corrigendum source and target mutation boundary"),
            ("mutation_boundary_proof_ref", "proof-corr-1"),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.CORRIGENDUM_UNSUPPORTED_PATCH_RESOLUTION")
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
            *_sparse_slot_proof_values(),
        ),
        source_bytes=source,
    )
    spec = get_claim_kind_spec("fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION")
    assert spec is not None
    assert spec.entailment_validator is not None
    assert spec.entailment_validator(claim, source).passed is True

    state = ClaimState(
        claim_id=claim.claim_id,
        claim_state_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
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


def test_semantic_xml_manual_frontier_claim_requires_matching_phase_gate() -> None:
    source = b"<p>literal sparse slot source quote</p>"
    claim = _claim(
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        target=(
            *_common_target("SPARSE_ITEM_BODY_MISSING"),
            ("frontier_ref", "fi-frontier-sparse-1"),
        ),
        value=(
            ("source_quote", "sparse slot source quote"),
            ("candidate_slots", ("section:35/subsection:1/item:5",)),
            ("selected_slot", "section:35/subsection:1/item:5"),
            ("old_text_precondition", "old text"),
            *_sparse_slot_proof_values(),
        ),
        source_bytes=source,
    )
    state = ClaimState(
        claim_id=claim.claim_id,
        claim_state_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.ENTAILMENT_VERIFIED,
        confidence=ClaimConfidence.HIGH,
        last_updated=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    gate = PhaseLocalReplayGate(
        gate_id="fi-sparse-gate-1",
        jurisdiction="fi",
        claim_id=claim.claim_id,
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="fi-frontier-sparse-1",
        owner_phase="typed_elaboration",
        authorization_rule_id="fi_sparse_slot_phase_gate_v1",
        required_proofs=(
            "target_uniqueness_proof",
            "payload_identity_proof",
            "mutation_boundary_proof",
        ),
        satisfied_proofs=(
            "target_uniqueness_proof",
            "payload_identity_proof",
            "mutation_boundary_proof",
        ),
        candidate_operation_family="sparse_item_payload_resolution",
        candidate_targets=("section:35/subsection:1/item:5",),
        detail={
            "rejected_candidate_slots": [],
            "mutation_boundary_proof_ref": "proof-sparse-1",
        },
    )

    decision, _event = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        build_id="test-build",
        precedence_registry=PrecedenceRegistry(rules=(), source_path="<test>"),
        phase_replay_gate=gate,
    )

    assert decision.authorized is True
    assert decision.replay_authorized is True
    assert decision.reason_code == "accepted_strict_attested_phase_replay_authorized"


def test_semantic_xml_manual_frontier_claim_rejects_mismatched_phase_gate() -> None:
    source = b"<p>literal sparse slot source quote</p>"
    claim = _claim(
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        target=(
            *_common_target("SPARSE_ITEM_BODY_MISSING"),
            ("frontier_ref", "fi-frontier-sparse-1"),
        ),
        value=(
            ("source_quote", "sparse slot source quote"),
            ("candidate_slots", ("section:35/subsection:1/item:5",)),
            ("selected_slot", "section:35/subsection:1/item:5"),
            ("old_text_precondition", "old text"),
            *_sparse_slot_proof_values(),
        ),
        source_bytes=source,
    )
    state = ClaimState(
        claim_id=claim.claim_id,
        claim_state_status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.VERIFIED_MANUAL,
        validator_status=ValidatorStatus.ENTAILMENT_VERIFIED,
        confidence=ClaimConfidence.HIGH,
        last_updated=datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
    )
    gate = PhaseLocalReplayGate(
        gate_id="fi-sparse-gate-2",
        jurisdiction="fi",
        claim_id=claim.claim_id,
        claim_kind="fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION",
        frontier_ref="different-frontier",
        owner_phase="typed_elaboration",
        authorization_rule_id="fi_sparse_slot_phase_gate_v1",
        required_proofs=("target_uniqueness_proof",),
        satisfied_proofs=("target_uniqueness_proof",),
    )

    decision, _event = derive_composition_decision(
        claim=claim,
        state=state,
        profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        build_id="test-build",
        precedence_registry=PrecedenceRegistry(rules=(), source_path="<test>"),
        phase_replay_gate=gate,
    )

    assert decision.authorized is False
    assert decision.replay_authorized is False
    assert decision.reason_code == "rejected_phase_replay_gate_frontier_mismatch"

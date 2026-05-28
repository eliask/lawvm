from typing import Any, cast

import pytest

from lawvm.core.evidence_contracts import (
    CorpusFindingEvidenceRow,
    CorpusOperationEvidenceRow,
    CorpusRowStatus,
    EvidenceSummary,
    evidence_row_kind,
    evidence_rule_ids,
    validate_corpus_finding_evidence_row,
    validate_corpus_operation_evidence_row,
)
from lawvm.contracts import ArtifactEnvelope, ProcessingStatus, to_wire_jsonable
from lawvm.core.replay_contracts import ReplayAmendmentStep, ReplaySummary, ReplayTextView
from lawvm.core.verification_contracts import (
    CoverageAttribution,
    DivergenceRecord,
    DivergencePartition,
    FilteredDivergenceRecord,
    VerifyIssue,
    VerifySummary,
)


def test_processing_status_validates_degraded_blockers() -> None:
    assert ProcessingStatus(kind="partial", blockers=cast(Any, ["missing.source"])).blockers == (
        "missing.source",
    )

    with pytest.raises(ValueError, match="requires at least one blocker"):
        ProcessingStatus(kind="partial")

    with pytest.raises(ValueError, match="must not carry blockers"):
        ProcessingStatus(kind="complete", blockers=("unexpected",))


def test_artifact_envelope_validates_identity_fields() -> None:
    with pytest.raises(ValueError, match="schema"):
        ArtifactEnvelope(schema="", producer="tests", version="1", payload={})


def test_replay_summary_to_dict_is_json_friendly() -> None:
    summary = ReplaySummary(
        jurisdiction="no",
        base_id="no/lov/2005-05-20-28",
        as_of="2026-03-29",
        amendment_count=3,
        applied_count=2,
        op_count=5,
        steps=(
            ReplayAmendmentStep(source_id="2006-01-01-1", status="applied", op_count=2),
            ReplayAmendmentStep(source_id="2007-01-01-2", status="skipped", op_count=0),
        ),
        text_view=ReplayTextView(content="hello"),
    )

    data = summary.to_dict()

    assert data["jurisdiction"] == "no"
    assert data["steps"][0]["source_id"] == "2006-01-01-1"
    assert data["text_view"]["content"] == "hello"


def test_verify_summary_to_dict_embeds_nested_records() -> None:
    summary = VerifySummary(
        jurisdiction="ee",
        base_id="113032019003",
        as_of="2022-06-01",
        consistent=False,
        issue_count=1,
        divergence_count=1,
        issues=(VerifyIssue(code="parse.bad", message="bad parse", stage="parse"),),
        divergences=(
            DivergenceRecord(
                address="section:1",
                kind="MISMATCH",
                replay_text="a",
                oracle_text="b",
                score=0.5,
                touched=True,
            ),
        ),
        coverage=CoverageAttribution(
            touched_divergence_count=1,
            untouched_divergence_count=0,
        ),
    )

    data = summary.to_dict()

    assert data["issues"][0]["code"] == "parse.bad"
    assert data["divergences"][0]["address"] == "section:1"
    assert data["coverage"]["touched_divergence_count"] == 1


def test_verify_contracts_reject_invalid_envelope_shapes() -> None:
    with pytest.raises(ValueError, match="VerifyIssue.code"):
        VerifyIssue(code="", message="bad")

    with pytest.raises(ValueError, match="severity"):
        VerifyIssue(code="parse.bad", message="bad", severity=cast(Any, "fatal"))

    with pytest.raises(ValueError, match="score"):
        DivergenceRecord(address="section:1", kind="MISMATCH", score=1.5)

    with pytest.raises(ValueError, match="rule_id"):
        FilteredDivergenceRecord(
            divergence=DivergenceRecord(address="section:1", kind="MISMATCH"),
            rule_id="",
            reason="covered by child",
        )

    with pytest.raises(ValueError, match="touched_path_count"):
        CoverageAttribution(touched_path_count=-1)

    with pytest.raises(ValueError, match="jurisdiction"):
        VerifySummary(jurisdiction="", base_id="base")


def test_divergence_partition_preserves_filtered_rule_evidence() -> None:
    divergence = DivergenceRecord(address="section:1", kind="MISMATCH")

    partition = DivergencePartition(
        primary=[],
        filtered=[
            FilteredDivergenceRecord(
                divergence=divergence,
                rule_id="verify.prefix_descendant_suppressed",
                reason="parent divergence covered by child divergence",
            )
        ],
    )

    assert partition.primary == []
    assert partition.filtered[0].divergence is divergence
    assert partition.filtered[0].rule_id == "verify.prefix_descendant_suppressed"


def test_evidence_summary_to_dict_preserves_tuple_fields() -> None:
    summary = EvidenceSummary(
        jurisdiction="fi",
        base_id="1991/1707",
        primary_tier="oracle_ready",
        claim_count=3,
        tiers=("oracle_ready", "strict_fail"),
        claim_kinds=("oracle_stale", "html_xml_drift"),
        trigger_sources=("frontend",),
        artifact_families=("oracle",),
    )

    data = summary.to_dict()

    assert data["primary_tier"] == "oracle_ready"
    assert data["tiers"] == ("oracle_ready", "strict_fail")
    assert data["claim_kinds"] == ("oracle_stale", "html_xml_drift")


def test_corpus_operation_evidence_row_to_dict_preserves_unsupported_status() -> None:
    row = CorpusOperationEvidenceRow(
        row_id="row-1",
        frontend_id="open_law_maryland",
        source_artifact_id="editorial-actions/x.xml",
        effect_family="expire",
        status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition="record_unsupported",
        finding_ids=("open_law_expire_lifecycle_not_replayed",),
    )

    data = row.to_dict()

    assert data["status"] == "unsupported"
    assert data["finding_ids"] == ("open_law_expire_lifecycle_not_replayed",)
    assert validate_corpus_operation_evidence_row(data) == ()


def test_corpus_finding_evidence_row_to_dict_is_json_friendly() -> None:
    row = CorpusFindingEvidenceRow(
        finding_id="row-1:finding",
        frontend_id="open_law_maryland",
        family="unsupported",
        rule_id="open_law_expire_lifecycle_not_replayed",
        phase="lifecycle",
        message="recorded",
        strict_disposition="block",
        quirks_disposition="record",
        blocking=True,
        evidence={"path": ("a", "b")},
    )

    data = row.to_dict()

    assert data["rule_id"] == "open_law_expire_lifecycle_not_replayed"
    assert data["evidence"] == {"path": ("a", "b")}
    assert validate_corpus_finding_evidence_row(data) == ()


def test_corpus_operation_evidence_validation_rejects_unexplained_non_claim() -> None:
    issues = validate_corpus_operation_evidence_row({
        "row_id": "row-1",
        "frontend_id": "starter",
        "source_artifact_id": "act.xml",
        "status": "unsupported",
        "blocking": True,
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "finding_ids": (),
        "detail": {},
    })

    assert "unsupported row must carry finding_ids or reason-bearing detail" in issues
    assert "blocking row must have blocking strict_disposition" in issues


def test_corpus_operation_evidence_row_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError, match="unsupported row must carry finding_ids"):
        CorpusOperationEvidenceRow(
            row_id="row-1",
            frontend_id="starter",
            source_artifact_id="act.xml",
            status=CorpusRowStatus.UNSUPPORTED,
            blocking=True,
            strict_disposition="block",
            quirks_disposition="record",
        )


def test_corpus_operation_evidence_validation_rejects_blocking_match_without_justification() -> None:
    issues = validate_corpus_operation_evidence_row({
        "row_id": "row-1",
        "frontend_id": "starter",
        "source_artifact_id": "act.xml",
        "status": "matched",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "finding_ids": ("positive_projection",),
        "detail": {},
    })

    assert issues == ("matched row cannot be blocking without blocking_justification detail",)


def test_corpus_finding_evidence_row_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError, match="finding_id is required"):
        CorpusFindingEvidenceRow(
            finding_id="",
            frontend_id="starter",
            family="unsupported",
            rule_id="starter.rule",
            phase="P1",
            message="bad",
            strict_disposition="record",
            quirks_disposition="record",
        )


def test_corpus_finding_evidence_validation_rejects_bad_shapes() -> None:
    issues = validate_corpus_finding_evidence_row({
        "finding_id": "",
        "frontend_id": "starter",
        "rule_id": "starter.rule",
        "phase": "P1",
        "message": "bad",
        "strict_disposition": "record",
        "quirks_disposition": "record",
        "blocking": "yes",
        "evidence": [],
        "related_row_ids": "row-1",
    })

    assert "finding_id is required" in issues
    assert "blocking must be a boolean" in issues
    assert "evidence must be a mapping" in issues
    assert "related_row_ids must be a list or tuple" in issues


def test_evidence_rule_ids_extracts_stable_detail_rule_ids() -> None:
    row = CorpusOperationEvidenceRow(
        row_id="row-1",
        frontend_id="new_zealand",
        source_artifact_id="act_public_2020_1",
        status=CorpusRowStatus.ACCEPTED,
        strict_disposition="candidate_only",
        quirks_disposition="candidate_only",
        finding_ids=("nz_existing_finding",),
        detail={
            "reason": "candidate canonical effect emitted but not replayed",
            "blocking_rule_id": "nz_effect_readiness_amendment_semantics_not_extracted",
            "operation_target_blocking_rule_id": "nz_target_address_duplicate_source_path",
            "effect_blocking_rule_id": "nz_operation_surface_effect_lowering_not_implemented",
            "candidate_witness_rule_id": "nz_repeal_candidate_from_history_note_payload_witness",
            "preflight_blocking_rule_id": "nz_effect_preflight_candidate_operation_missing",
            "declared_recovery_rule_ids": ["section_move_replace_destination_rebind"],
            "declared_migration_rule_ids": (),
            "matched_allowance_rule_ids": ("section_materialization_root_move_destination_rebind",),
        },
    )

    assert evidence_rule_ids(row.to_dict()) == {
        "nz_existing_finding",
        "nz_effect_readiness_amendment_semantics_not_extracted",
        "nz_target_address_duplicate_source_path",
        "nz_operation_surface_effect_lowering_not_implemented",
        "nz_repeal_candidate_from_history_note_payload_witness",
        "nz_effect_preflight_candidate_operation_missing",
        "section_move_replace_destination_rebind",
        "section_materialization_root_move_destination_rebind",
    }


def test_evidence_rule_ids_allows_stable_reason_rule_ids() -> None:
    row = CorpusOperationEvidenceRow(
        row_id="row-1",
        frontend_id="starter",
        source_artifact_id="act.xml",
        status=CorpusRowStatus.UNSUPPORTED,
        blocking=True,
        strict_disposition="block",
        quirks_disposition="record",
        detail={"reason": "starter.unsupported.v1"},
    )

    assert evidence_rule_ids(row.to_dict()) == {"starter.unsupported.v1"}


def test_evidence_rule_ids_scans_detail_and_evidence_maps_when_both_exist() -> None:
    row = {
        "row_id": "row-1",
        "frontend_id": "starter",
        "source_artifact_id": "act.xml",
        "status": "unsupported",
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "detail": {"candidate_witness_rule_id": "starter.detail_witness"},
        "evidence": {"blocking_rule_id": "starter.evidence_blocker"},
    }

    assert evidence_rule_ids(row) == {"starter.detail_witness", "starter.evidence_blocker"}


def test_evidence_row_kind_classifies_shared_evidence_rows() -> None:
    assert evidence_row_kind({"row_id": "operation-1"}) == "operation"
    assert evidence_row_kind({"finding_id": "finding-1"}) == "finding"
    assert evidence_row_kind({"rule_id": "starter.unsupported.v1"}) == "finding"


def test_to_wire_jsonable_normalizes_nested_runtime_shapes() -> None:
    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    got = to_wire_jsonable({
        "tuple": (1, 2),
        "set": {"a", "b"},
        "nested": {"value": Weird()},
    })

    assert got["tuple"] == [1, 2]
    assert sorted(got["set"]) == ["a", "b"]
    assert got["nested"]["value"] == "<weird>"


def test_artifact_envelope_to_wire_jsonable_serializes_schema_and_status() -> None:
    envelope = ArtifactEnvelope(
        schema="lawvm.test",
        producer="tests",
        version="1",
        payload={
            "body": {"kind": "content", "text": "hello"},
            "tags": {"a", "b"},
        },
        status=ProcessingStatus(kind="partial", blockers=("missing.source",)),
    )

    got = to_wire_jsonable(envelope)

    assert got["schema"] == "lawvm.test"
    assert got["producer"] == "tests"
    assert got["version"] == "1"
    assert got["payload"]["body"]["text"] == "hello"
    assert sorted(got["payload"]["tags"]) == ["a", "b"]
    assert got["status"] == {"kind": "partial", "blockers": ["missing.source"]}

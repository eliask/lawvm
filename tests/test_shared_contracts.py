from lawvm.core.evidence_contracts import CorpusFindingEvidenceRow, CorpusOperationEvidenceRow, CorpusRowStatus, EvidenceSummary
from lawvm.contracts import ArtifactEnvelope, ProcessingStatus, to_wire_jsonable
from lawvm.core.replay_contracts import ReplayAmendmentStep, ReplaySummary, ReplayTextView
from lawvm.core.verification_contracts import (
    CoverageAttribution,
    DivergenceRecord,
    VerifyIssue,
    VerifySummary,
)


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
        finding_ids=("open_law_expire_lifecycle_not_replayed",),
    )

    data = row.to_dict()

    assert data["status"] == "unsupported"
    assert data["finding_ids"] == ("open_law_expire_lifecycle_not_replayed",)


def test_corpus_finding_evidence_row_to_dict_is_json_friendly() -> None:
    row = CorpusFindingEvidenceRow(
        finding_id="row-1:finding",
        frontend_id="open_law_maryland",
        family="unsupported",
        rule_id="open_law_expire_lifecycle_not_replayed",
        phase="lifecycle",
        message="recorded",
        evidence={"path": ("a", "b")},
    )

    data = row.to_dict()

    assert data["rule_id"] == "open_law_expire_lifecycle_not_replayed"
    assert data["evidence"] == {"path": ("a", "b")}


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

"""EU typed-op skip census evidence.

The EU CTSF oracle-touch lane records ``eu_replay_typed_op_skip`` rows for
conserved-apply rejected ops. These tests pin the I1 diagnostic census surface:
the manifest evidence must preserve the apply adjudication's typed reason rather
than collapsing every skip to a generic reason string.
"""

from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.eu.pipeline import apply_eu_ops_conserved
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.tools.eu_anchor_manifest import (
    EU_TYPED_SKIP_BUCKET_ANNEX_LANE_TARGET_ABSENT,
    EU_TYPED_SKIP_BUCKET_EMPTY_TARGET_LABEL,
    EU_TYPED_SKIP_BUCKET_TARGET_KIND_LABEL_ABSENT,
    EU_TYPED_SKIP_BUCKET_UNBUCKETED,
    EUTypedOpSkipBucketSummary,
    EUOracleAttribution,
    EUReplayObservation,
    VERDICT_APPLY_RAISE,
    VERDICT_TYPED_OP_SKIP,
    _eu_typed_op_skip_evidence_record,
    summarize_eu_typed_op_skip_buckets,
)


def _baseline_statute() -> IRStatute:
    return IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Section 1"),),
        ),
    )


def test_eu_typed_skip_census_preserves_empty_target_label_detail() -> None:
    """Production-path liveness: real conserved apply -> manifest skip bucket."""
    op = LegalOperation(
        op_id="eu-annex-empty-label",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("annex", ""),), root="supplements"),
        payload=IRNode(kind=IRNodeKind.SECTION, label="", text="replacement annex"),
        source=OperationSource(statute_id="32026R0001"),
    )
    adjudications: list[CompileAdjudication] = []

    result = apply_eu_ops_conserved(
        _baseline_statute(), [op], adjudications_out=adjudications
    )

    assert len(result.skipped_items) == 1
    assert len(adjudications) == 1
    evidence = _eu_typed_op_skip_evidence_record(result.skipped_items[0], adjudications[0])
    assert evidence.skip_bucket == EU_TYPED_SKIP_BUCKET_EMPTY_TARGET_LABEL
    assert evidence.reason_code == result.skipped_items[0].reason_code
    assert evidence.detail_reason_code == adjudications[0].detail["reason_code"]
    observation = EUReplayObservation(
        sid="32000R0000",
        section_key="eu_replay_target_not_found",
        verdict=VERDICT_TYPED_OP_SKIP,
        window="..2026-01-01",
        touching_amendments=("32026R0001",),
        evidence=evidence.render(),
        typed_skip_evidence=evidence,
    )
    assert observation.to_dict()["typed_skip_evidence"] == evidence.to_dict()


def test_eu_typed_skip_census_distinguishes_plain_absent_target() -> None:
    op = LegalOperation(
        op_id="eu-section-absent",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "99"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="99", text="replacement"),
        source=OperationSource(statute_id="32026R0001"),
    )
    adjudications: list[CompileAdjudication] = []

    result = apply_eu_ops_conserved(
        _baseline_statute(), [op], adjudications_out=adjudications
    )

    evidence = _eu_typed_op_skip_evidence_record(result.skipped_items[0], adjudications[0])
    assert evidence.skip_bucket == EU_TYPED_SKIP_BUCKET_TARGET_KIND_LABEL_ABSENT
    assert evidence.reason_code == result.skipped_items[0].reason_code


def test_eu_typed_skip_census_buckets_annex_parent_absent_by_lane() -> None:
    op = LegalOperation(
        op_id="eu-annex-point-parent-absent",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(
            path=(("annex", "I"), ("point", "9a")),
            root="supplements",
        ),
        payload=IRNode(kind=IRNodeKind.ITEM, label="9a", text="9a. Inserted point."),
        source=OperationSource(statute_id="32026R0001"),
    )
    adjudications: list[CompileAdjudication] = []

    result = apply_eu_ops_conserved(
        _baseline_statute(), [op], adjudications_out=adjudications
    )

    evidence = _eu_typed_op_skip_evidence_record(result.skipped_items[0], adjudications[0])
    assert evidence.skip_bucket == EU_TYPED_SKIP_BUCKET_ANNEX_LANE_TARGET_ABSENT
    assert evidence.reason_code == "eu_replay_parent_not_found"


def test_eu_typed_skip_bucket_summary_uses_typed_carrier() -> None:
    target_missing = _eu_typed_op_skip_evidence_record(
        apply_eu_ops_conserved(
            _baseline_statute(),
            [
                LegalOperation(
                    op_id="eu-section-absent",
                    sequence=1,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=(("section", "99"),)),
                    payload=IRNode(kind=IRNodeKind.SECTION, label="99", text="replacement"),
                    source=OperationSource(statute_id="32026R0001"),
                )
            ],
            adjudications_out=[],
        ).skipped_items[0],
        None,
    )
    observations = (
        EUReplayObservation(
            sid="32000R0000",
            section_key="one",
            verdict=VERDICT_TYPED_OP_SKIP,
            window="..2026-01-01",
            touching_amendments=("32026R0001",),
            evidence=target_missing.render(),
            typed_skip_evidence=target_missing,
        ),
        EUReplayObservation(
            sid="32000R0000",
            section_key="two",
            verdict=VERDICT_TYPED_OP_SKIP,
            window="..2026-01-01",
            touching_amendments=("32026R0002",),
            evidence="",
        ),
        EUReplayObservation(
            sid="32000R0000",
            section_key="apply",
            verdict=VERDICT_APPLY_RAISE,
            window="..2026-01-01",
            touching_amendments=("32026R0003",),
            evidence="",
        ),
    )

    summary = summarize_eu_typed_op_skip_buckets(observations)

    assert summary == (
        EUTypedOpSkipBucketSummary(
            skip_bucket=EU_TYPED_SKIP_BUCKET_TARGET_KIND_LABEL_ABSENT,
            count=1,
        ),
        EUTypedOpSkipBucketSummary(
            skip_bucket=EU_TYPED_SKIP_BUCKET_UNBUCKETED,
            count=1,
        ),
    )


def test_oracle_attribution_exposes_typed_skip_bucket_census() -> None:
    evidence = _eu_typed_op_skip_evidence_record(
        apply_eu_ops_conserved(
            _baseline_statute(),
            [
                LegalOperation(
                    op_id="eu-section-absent",
                    sequence=1,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=(("section", "99"),)),
                    payload=IRNode(kind=IRNodeKind.SECTION, label="99", text="replacement"),
                    source=OperationSource(statute_id="32026R0001"),
                )
            ],
            adjudications_out=[],
        ).skipped_items[0],
        None,
    )
    attribution = EUOracleAttribution(
        sid="32000R0000",
        anchors=(),
        observations=(),
        eu_observations=(
            EUReplayObservation(
                sid="32000R0000",
                section_key="one",
                verdict=VERDICT_TYPED_OP_SKIP,
                window="..2026-01-01",
                touching_amendments=("32026R0001",),
                evidence=evidence.render(),
                typed_skip_evidence=evidence,
            ),
            EUReplayObservation(
                sid="32000R0000",
                section_key="apply",
                verdict=VERDICT_APPLY_RAISE,
                window="..2026-01-01",
                touching_amendments=("32026R0002",),
                evidence="",
            ),
        ),
    )

    assert attribution.typed_skip_bucket_counts() == summarize_eu_typed_op_skip_buckets(
        attribution.eu_observations
    )

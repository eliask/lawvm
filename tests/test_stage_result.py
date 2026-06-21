"""Tests for the canonical core stage-result types (Wave 0 FOUNDATION).

Covers: construction, empty/identity defaults (total-accounting), the authority
firewall default (``replay_authorized=False``), the coverage partition account,
and the PartitionResult-vs-FilterResult relationship. See
``notes_internal/STAGERESULT_ENDGAME.md``.
"""

from __future__ import annotations

import pytest

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.phase_result import Finding
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.stage_result import (
    EMPTY_COVERAGE,
    EMPTY_EVIDENCE,
    NEUTRAL_AUTHORITY,
    AuthoritySurface,
    CoverageCertificate,
    EvidenceBundle,
    PartitionResult,
    Residual,
    StageResult,
)


# ---------------------------------------------------------------------------
# Identity / empty defaults — total-accounting "nothing to report".
# ---------------------------------------------------------------------------


def test_stage_result_empty_defaults_construct_trivially() -> None:
    result: StageResult[int] = StageResult(value=7)
    assert result.value == 7
    assert result.evidence is EMPTY_EVIDENCE
    assert result.residuals == ()
    assert result.findings == ()
    assert result.coverage is EMPTY_COVERAGE
    assert result.authority is NEUTRAL_AUTHORITY
    # empty residuals == fully owned == no blocking residue
    assert result.has_blocking_residual is False


def test_empty_evidence_is_empty() -> None:
    assert EMPTY_EVIDENCE.is_empty is True
    assert EvidenceBundle().is_empty is True


def test_empty_coverage_is_total_and_clean() -> None:
    assert EMPTY_COVERAGE.is_partition() is True
    assert EMPTY_COVERAGE.is_clean is True
    assert EMPTY_COVERAGE.partition_total == 0


# ---------------------------------------------------------------------------
# Authority firewall — neutral surface is non-authoritative by construction.
# ---------------------------------------------------------------------------


def test_neutral_authority_is_not_replay_authorized() -> None:
    assert NEUTRAL_AUTHORITY.is_neutral is True
    assert NEUTRAL_AUTHORITY.replay_authorized is False
    assert AuthoritySurface().replay_authorized is False


def test_stage_result_default_authority_is_not_replay_authorized() -> None:
    # The firewall in the DEFAULTS: a stage that attaches no authority is
    # non-authoritative without the author having to set anything.
    assert StageResult(value=object()).authority.replay_authorized is False


def test_authority_surface_replay_authority_requires_explicit_grant() -> None:
    granting = ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="granted",
        authorization_rule_id="rule.x",
        owner_phase="apply",
        strict_disposition="record",
        required_proofs=(),
        safe_default="record",
    )
    surface = AuthoritySurface(authorization=granting)
    assert surface.replay_authorized is True
    assert surface.is_neutral is False

    non_granting = ExecutionAuthorization(
        executable=False,
        replay_authorized=False,
        authorization_status="blocked",
        authorization_rule_id="rule.y",
        owner_phase="apply",
        strict_disposition="block",
        required_proofs=("proof.a",),
        safe_default="block",
    )
    assert AuthoritySurface(authorization=non_granting).replay_authorized is False


def test_authority_surface_rejects_wrong_types() -> None:
    with pytest.raises(ValueError):
        AuthoritySurface(authorization="nope")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        AuthoritySurface(source_admission="nope")  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# Residual.
# ---------------------------------------------------------------------------


def test_residual_requires_kind_and_reason() -> None:
    with pytest.raises(ValueError):
        Residual(kind="", reason="x")
    with pytest.raises(ValueError):
        Residual(kind="x", reason="  ")


def test_residual_defaults_blocking() -> None:
    residual = Residual(kind="out_of_scope", reason="not in this lens")
    assert residual.blocking is True
    assert residual.char_start is None
    benign = Residual(kind="benign_uninterpreted", reason="prose", blocking=False)
    assert benign.blocking is False


# ---------------------------------------------------------------------------
# CoverageCertificate — the partition account.
# ---------------------------------------------------------------------------


def test_coverage_certificate_partition_and_clean() -> None:
    cert = CoverageCertificate(
        unit="tokens", total=10, owned=8, benign=1, residual=1, violation=0
    )
    assert cert.partition_total == 10
    assert cert.is_partition() is True
    assert cert.is_clean is True


def test_coverage_certificate_violation_is_not_clean() -> None:
    cert = CoverageCertificate(
        unit="tokens", total=10, owned=7, benign=1, residual=1, violation=1
    )
    assert cert.is_clean is False
    assert cert.is_partition() is True


def test_coverage_certificate_non_total_when_classes_do_not_sum() -> None:
    cert = CoverageCertificate(unit="tokens", total=10, owned=3)
    assert cert.is_partition() is False


def test_coverage_certificate_open_account_not_a_partition() -> None:
    # totality not claimed -> not a partition even if balanced
    cert = CoverageCertificate(total=5, owned=5, totality_claimed=False)
    assert cert.is_partition() is False


def test_coverage_certificate_rejects_negative() -> None:
    with pytest.raises(ValueError):
        CoverageCertificate(total=-1)


# ---------------------------------------------------------------------------
# EvidenceBundle — composes typed witnesses.
# ---------------------------------------------------------------------------


def test_evidence_bundle_accepts_witness_types() -> None:
    witness = SourceWitness(source_role="oracle")
    digest = DigestWitness(digest_algorithm="sha256", digest="abc")
    bundle = EvidenceBundle(witnesses=(witness, digest))
    assert bundle.is_empty is False
    assert len(bundle.witnesses) == 2


def test_evidence_bundle_rejects_non_witness() -> None:
    with pytest.raises(ValueError):
        EvidenceBundle(witnesses=("not a witness",))  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# StageResult — full construction + validation.
# ---------------------------------------------------------------------------


def _finding() -> Finding:
    return Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="test",
        detail={"k": 1},
        blocking=False,
    )


def test_stage_result_full_construction() -> None:
    residual = Residual(kind="typed_residual", reason="explicit residue", blocking=False)
    cert = CoverageCertificate(unit="spans", total=3, owned=2, residual=1)
    result = StageResult(
        value=("a", "b"),
        evidence=EvidenceBundle(witnesses=(SourceWitness(source_role="oracle"),)),
        residuals=(residual,),
        findings=(_finding(),),
        coverage=cert,
    )
    assert result.value == ("a", "b")
    assert result.residuals == (residual,)
    assert result.has_blocking_residual is False  # the only residual is non-blocking
    assert result.coverage.is_partition() is True


def test_stage_result_blocking_residual_flag() -> None:
    blocking = Residual(kind="unowned_violation", reason="unaccounted signal")
    result = StageResult(value=None, residuals=(blocking,))
    assert result.has_blocking_residual is True


def test_stage_result_rejects_wrong_member_types() -> None:
    with pytest.raises(ValueError):
        StageResult(value=1, residuals=("nope",))  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        StageResult(value=1, findings=("nope",))  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        StageResult(value=1, evidence="nope")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        StageResult(value=1, coverage="nope")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        StageResult(value=1, authority="nope")  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# PartitionResult — composes FilterResult, adds residuals/findings/coverage.
# ---------------------------------------------------------------------------


def test_partition_result_relationship_to_filter_result() -> None:
    fr: FilterResult[int] = FilterResult(
        accepted_items=(1, 2),
        rejected_items=(RejectedItem(item=3, reason="out of scope"),),
    )
    part = PartitionResult(filter_result=fr)
    # the accepted/rejected accessors delegate to the wrapped FilterResult
    assert part.accepted == (1, 2)
    assert len(part.rejected) == 1
    assert part.rejected[0].item == 3
    # composition, not aliasing
    assert isinstance(part.filter_result, FilterResult)
    assert not isinstance(part, FilterResult)


def test_partition_result_empty_defaults() -> None:
    part: PartitionResult[int] = PartitionResult()
    assert part.accepted == ()
    assert part.rejected == ()
    assert part.residuals == ()
    assert part.findings == ()
    assert part.coverage is EMPTY_COVERAGE
    assert part.has_blocking_residual is False


def test_partition_result_carries_residuals_and_coverage() -> None:
    residual = Residual(kind="out_of_scope", reason="filtered candidate")
    cert = CoverageCertificate(unit="candidates", total=5, owned=4, residual=1)
    part = PartitionResult(
        filter_result=FilterResult(accepted_items=(1,)),
        residuals=(residual,),
        findings=(_finding(),),
        coverage=cert,
    )
    assert part.has_blocking_residual is True
    assert part.coverage.is_partition() is True
    assert len(part.findings) == 1


def test_partition_result_rejects_wrong_member_types() -> None:
    with pytest.raises(ValueError):
        PartitionResult(filter_result="nope")  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        PartitionResult(residuals=("nope",))  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        PartitionResult(findings=("nope",))  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError):
        PartitionResult(coverage="nope")  # ty: ignore[invalid-argument-type]

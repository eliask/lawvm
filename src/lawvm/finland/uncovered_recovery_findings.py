"""Shared finding/key carriers for Finland uncovered recovery."""
from __future__ import annotations

from dataclasses import dataclass, field

from lawvm.core.phase_result import Finding
from lawvm.finland.body_coverage_findings import (
    coverage_ignored_unit_finding as _coverage_ignored_unit_finding_impl,
    coverage_rejected_claim_finding as _coverage_rejected_claim_finding_impl,
    coverage_unresolved_gap_finding as _coverage_unresolved_gap_finding_impl,
    high_uncovered_body_degraded_finding as _high_uncovered_body_degraded_finding_impl,
)
from lawvm.finland.uncovered_body_findings import (
    strict_rejected_uncovered_body_finding as _strict_rejected_uncovered_body_finding_impl,
    uncovered_body_chapter_payload_mixed_finding as _uncovered_body_chapter_payload_mixed_finding_impl,
    uncovered_body_recovery_finding as _uncovered_body_recovery_finding_impl,
    uncovered_body_recovery_skipped_finding as _uncovered_body_recovery_skipped_finding_impl,
)


@dataclass(frozen=True, slots=True)
class UncoveredSkipKey:
    """Stable de-duplication key for uncovered-body skipped-recovery findings."""

    reason: str
    part: str
    chapter: str
    section: str


@dataclass(frozen=True, slots=True)
class RecoveryFindingKey:
    """Stable de-duplication key for uncovered-body recovery findings."""

    kind: str
    target_norm: str
    target_chapter: str
    target_part: str
    op_id: str


@dataclass(frozen=True, slots=True)
class KumotaanRecoveryFindingKey:
    """Stable de-duplication key for uncovered kumotaan recovery findings."""

    kind: str
    target_unit_kind: str
    target_norm: str
    target_chapter: str


@dataclass(frozen=True, slots=True)
class UncoveredBodyRecoveryFindingRequest:
    """Evidence fields for an uncovered-body recovery obligation finding."""

    op_id: str
    source_statute: str
    target_unit_kind: str
    target_norm: str
    target_chapter: str | None = None
    target_part: str | None = None


def _strict_rejected_uncovered_body_finding(
    *,
    source_statute: str,
    stage: str,
) -> Finding:
    return _strict_rejected_uncovered_body_finding_impl(
        source_statute=source_statute,
        stage=stage,
    )


def _uncovered_body_recovery_finding(
    request: UncoveredBodyRecoveryFindingRequest,
) -> Finding | None:
    return _uncovered_body_recovery_finding_impl(
        op_id=request.op_id,
        source_statute=request.source_statute,
        target_unit_kind=request.target_unit_kind,
        target_norm=request.target_norm,
        target_chapter=request.target_chapter,
        target_part=request.target_part,
    )


def _uncovered_body_recovery_skipped_finding(
    *,
    source_statute: str,
    target_section: str,
    reason: str,
    target_chapter: str | None = None,
    target_part: str | None = None,
) -> Finding:
    return _uncovered_body_recovery_skipped_finding_impl(
        source_statute=source_statute,
        target_section=target_section,
        reason=reason,
        target_chapter=target_chapter,
        target_part=target_part,
    )


def _uncovered_body_chapter_payload_mixed_finding(
    *,
    source_statute: str,
    target_chapter: str,
    adopted_count: int,
    owned_count: int,
) -> Finding:
    return _uncovered_body_chapter_payload_mixed_finding_impl(
        source_statute=source_statute,
        target_chapter=target_chapter,
        adopted_count=adopted_count,
        owned_count=owned_count,
    )


@dataclass(frozen=True, slots=True)
class HighUncoveredBodyDegradedFindingRequest:
    """Evidence fields for a high-uncovered-body degraded-confidence finding."""

    source_statute: str
    uncovered_count: int
    total_units: int
    uncov_ratio: float
    confidence: float
    signals: list[str]


def _high_uncovered_body_degraded_finding(
    request: HighUncoveredBodyDegradedFindingRequest,
) -> Finding:
    return _high_uncovered_body_degraded_finding_impl(
        source_statute=request.source_statute,
        uncovered_count=request.uncovered_count,
        total_units=request.total_units,
        uncov_ratio=request.uncov_ratio,
        confidence=request.confidence,
        signals=request.signals,
    )


@dataclass(frozen=True, slots=True)
class CoverageIgnoredUnitFindingRequest:
    """Evidence fields for a coverage ignored-unit finding."""

    source_statute: str
    unit_kind: str
    reason: str
    observed_label: str | None
    parent_label: str | None
    evidence: tuple[str, ...]


def _coverage_ignored_unit_finding(
    request: CoverageIgnoredUnitFindingRequest,
) -> Finding:
    return _coverage_ignored_unit_finding_impl(
        source_statute=request.source_statute,
        unit_kind=request.unit_kind,
        reason=request.reason,
        observed_label=request.observed_label,
        parent_label=request.parent_label,
        evidence=request.evidence,
    )


def _coverage_rejected_claim_finding(
    *,
    source_statute: str,
    reason: str,
    evidence: tuple[str, ...],
) -> Finding:
    return _coverage_rejected_claim_finding_impl(
        source_statute=source_statute,
        reason=reason,
        evidence=evidence,
    )


@dataclass(frozen=True, slots=True)
class CoverageUnresolvedGapFindingRequest:
    """Evidence fields for a coverage unresolved-gap finding."""

    source_statute: str
    disposition: str
    unit_kind: str
    observed_label: str | None
    parent_label: str | None
    evidence: tuple[str, ...]


def _coverage_unresolved_gap_finding(
    request: CoverageUnresolvedGapFindingRequest,
) -> Finding:
    return _coverage_unresolved_gap_finding_impl(
        source_statute=request.source_statute,
        disposition=request.disposition,
        unit_kind=request.unit_kind,
        observed_label=request.observed_label,
        parent_label=request.parent_label,
        evidence=request.evidence,
    )


@dataclass(slots=True)
class KumotaanRecoveryFindingEmitter:
    """Deduplicating finding emitter for uncovered ``kumotaan`` recovery."""

    amendment_id: str
    findings_out: list[Finding] | None
    seen_recovery_findings: set[KumotaanRecoveryFindingKey] = field(default_factory=set)
    seen_skip_findings: set[UncoveredSkipKey] = field(default_factory=set)

    def append(
        self,
        *,
        op_id: str,
        target_unit_kind: str,
        target_norm: str,
        target_chapter: str | None = None,
    ) -> None:
        if self.findings_out is None:
            return
        finding = _uncovered_body_recovery_finding(
            UncoveredBodyRecoveryFindingRequest(
                op_id=op_id,
                source_statute=self.amendment_id,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter,
            )
        )
        if finding is None:
            return
        key = KumotaanRecoveryFindingKey(
            kind=str(finding.kind or ""),
            target_unit_kind=str(target_unit_kind or ""),
            target_norm=str(target_norm or ""),
            target_chapter=str(target_chapter or ""),
        )
        if key in self.seen_recovery_findings:
            return
        self.seen_recovery_findings.add(key)
        self.findings_out.append(finding)

    def append_skip(
        self,
        *,
        target_norm: str,
        reason: str,
        target_chapter: str | None = None,
    ) -> None:
        if self.findings_out is None:
            return
        key = UncoveredSkipKey(
            reason=reason,
            part="",
            chapter=target_chapter or "",
            section=target_norm,
        )
        if key in self.seen_skip_findings:
            return
        self.seen_skip_findings.add(key)
        self.findings_out.append(
            _uncovered_body_recovery_skipped_finding(
                source_statute=self.amendment_id,
                target_section=target_norm,
                target_chapter=target_chapter,
                reason=reason,
            )
        )

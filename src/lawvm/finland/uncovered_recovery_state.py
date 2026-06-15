"""Typed audit state for Finnish uncovered-body recovery."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from lawvm.core.ir import OperationSource
from lawvm.core.phase_result import Finding
from lawvm.finland.helpers import _norm_num_token, _roman_label_to_arabic
from lawvm.finland.ops import ResolvedOp
from lawvm.finland.uncovered_recovery_findings import (
    RecoveryFindingKey,
    UncoveredBodyRecoveryFindingRequest,
    UncoveredSkipKey,
    _uncovered_body_chapter_payload_mixed_finding,
    _uncovered_body_recovery_finding,
    _uncovered_body_recovery_skipped_finding,
)

FI_RECOVERY_UNCOVERED_BODY_RULE_ID = "fi.recovery.uncovered_body"


@dataclass(frozen=True, slots=True)
class UncoveredSectionKey:
    """Part/chapter/section key used by uncovered-body replay guards."""

    part: str
    chapter: str
    section: str


@dataclass(frozen=True, slots=True)
class RecoveredSectionKey:
    """Section recovered by uncovered-body synthesis, scoped by chapter."""

    section: str
    chapter: str


def uncovered_section_key(
    *,
    part: str | None,
    chapter: str | None,
    section: str,
) -> UncoveredSectionKey:
    """Return a normalized key for uncovered-body section ownership checks."""
    norm_part = _norm_num_token(part) if part else ""
    part_arabic = _roman_label_to_arabic(norm_part) if norm_part else None
    return UncoveredSectionKey(
        part=str(part_arabic) if part_arabic is not None else norm_part,
        chapter=_norm_num_token(chapter) if chapter else "",
        section=_norm_num_token(section),
    )


@dataclass(slots=True)
class UncoveredRecoveryGuards:
    """Mutable guard state for uncovered-body section recovery."""

    covered_sections: set[UncoveredSectionKey]
    chapter_payload_owned_sections: set[UncoveredSectionKey]
    relabel_destination_sections: set[UncoveredSectionKey]

    def mark_covered(
        self,
        *,
        part: str | None,
        chapter: str | None,
        section: str,
    ) -> None:
        self.covered_sections.add(
            uncovered_section_key(part=part, chapter=chapter, section=section)
        )

    def is_covered(
        self,
        *,
        part: str | None,
        chapter: str | None,
        section: str,
    ) -> bool:
        exact_key = uncovered_section_key(part=part, chapter=chapter, section=section)
        if (
            uncovered_section_key(part=None, chapter=None, section=section)
            in self.covered_sections
        ):
            return True
        if (
            exact_key.part
            and uncovered_section_key(part=exact_key.part, chapter=None, section=section)
            in self.covered_sections
        ):
            return True
        return bool(chapter and exact_key in self.covered_sections)

    def is_exact_covered(
        self,
        *,
        part: str | None,
        chapter: str | None,
        section: str,
    ) -> bool:
        return (
            uncovered_section_key(part=part, chapter=chapter, section=section)
            in self.covered_sections
        )

    def is_relabel_destination(
        self,
        *,
        part: str | None,
        chapter: str | None,
        section: str,
    ) -> bool:
        return (
            uncovered_section_key(part=part, chapter=chapter, section=section)
            in self.relabel_destination_sections
        )

    def is_chapter_payload_owned(
        self,
        *,
        part: str | None,
        chapter: str | None,
        section: str,
    ) -> bool:
        return (
            uncovered_section_key(part=part, chapter=chapter, section=section)
            in self.chapter_payload_owned_sections
        )


@dataclass(frozen=True, slots=True)
class UncoveredCandidateAudit:
    """One typed audit record per uncovered-body candidate decision.

    The observable decision trail for uncovered recovery: every candidate that
    enters the section-candidate recovery path produces exactly one of these,
    naming the resolved target and the disposition taken (recover/skip +
    reason). This is the spec-substitute for the recovery compiler.
    """

    section: str
    chapter: str
    part: str
    disposition: str  # "INSERT" | "REPLACE" | "MERGE" | "ADOPT" | "OWNED" | "SKIP"
    reason: str
    op_id: str = ""

    def __post_init__(self) -> None:
        if not self.section:
            raise ValueError("UncoveredCandidateAudit requires a non-empty section label")
        if not self.disposition:
            raise ValueError("UncoveredCandidateAudit requires a disposition")
        recovered = self.disposition in ("INSERT", "REPLACE", "MERGE", "ADOPT")
        if recovered and not self.op_id:
            raise ValueError(
                f"recovered disposition {self.disposition!r} requires an op_id"
            )

    def to_observation(self, *, source_statute: str) -> dict[str, object]:
        detail: dict[str, object] = {
            "rule_id": FI_RECOVERY_UNCOVERED_BODY_RULE_ID,
            "target_section": self.section,
            "target_chapter": self.chapter,
            "target_part": self.part,
            "disposition": self.disposition,
            "reason": self.reason,
        }
        if self.op_id:
            detail["op_id"] = self.op_id
        return {
            "kind": "APPLY.UNCOVERED_BODY_CANDIDATE_AUDIT",
            "source_statute": source_statute,
            "detail": detail,
        }


@dataclass(slots=True)
class RecoveryState:
    """Single typed container threading per-candidate uncovered recovery.

    Mutation is confined to explicit methods, so the recovery's effects are
    auditable in one place rather than scattered across local closures.
    """

    amendment_id: str
    op_source: Optional[OperationSource]
    findings_out: Optional[List[Finding]]
    guards: UncoveredRecoveryGuards

    result: List[ResolvedOp] = field(default_factory=list)
    audits: List[UncoveredCandidateAudit] = field(default_factory=list)
    recovered_section_keys: Set[RecoveredSectionKey] = field(default_factory=set)
    seen_recovery_findings: Set[RecoveryFindingKey] = field(default_factory=set)
    recorded_skip_keys: Set[UncoveredSkipKey] = field(default_factory=set)
    chapter_payload_dispositions: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def mark_covered(
        self,
        *,
        part: str | None,
        chapter: str | None,
        section: str,
    ) -> None:
        """Mark a section as covered so later candidates skip it (sequential)."""
        self.guards.mark_covered(part=part, chapter=chapter, section=section)

    def already_recovered(self, *, section: str, chapter: str | None) -> bool:
        """Whether this (section, chapter) was already recovered this run."""
        return (
            RecoveredSectionKey(
                section=_norm_num_token(section),
                chapter=_norm_num_token(chapter or ""),
            )
            in self.recovered_section_keys
        )

    def record_skip(
        self,
        reason: str,
        label: str,
        amend_chapter_label: Optional[str],
        amend_part_label: Optional[str] = None,
    ) -> None:
        """Record and de-duplicate a skipped-recovery finding plus audit row."""
        self.audits.append(
            UncoveredCandidateAudit(
                section=_norm_num_token(label),
                chapter=_norm_num_token(amend_chapter_label or ""),
                part=_norm_num_token(amend_part_label or ""),
                disposition="SKIP",
                reason=reason,
            )
        )
        if self.findings_out is None:
            return
        skip_key = UncoveredSkipKey(
            reason=reason,
            part=amend_part_label or "",
            chapter=amend_chapter_label or "",
            section=label,
        )
        if skip_key in self.recorded_skip_keys:
            return
        self.recorded_skip_keys.add(skip_key)
        self.findings_out.append(
            _uncovered_body_recovery_skipped_finding(
                source_statute=self.amendment_id,
                target_section=label,
                target_chapter=amend_chapter_label,
                target_part=amend_part_label,
                reason=reason,
            )
        )

    def append_recovered_rop(
        self,
        rop: ResolvedOp,
        *,
        disposition: str,
        reason: str,
    ) -> None:
        """Append a recovered ResolvedOp, recording its key, finding and audit."""
        target_scope = rop.resolved_target_scope_view
        self.recovered_section_keys.add(
            RecoveredSectionKey(
                section=_norm_num_token(target_scope.target_norm),
                chapter=_norm_num_token(target_scope.target_chapter or ""),
            )
        )
        self.result.append(rop)
        self.audits.append(
            UncoveredCandidateAudit(
                section=_norm_num_token(target_scope.target_norm),
                chapter=_norm_num_token(target_scope.target_chapter or ""),
                part=_norm_num_token(target_scope.target_part or ""),
                disposition=disposition,
                reason=reason,
                op_id=rop.op_id or "",
            )
        )
        if self.findings_out is None:
            return
        finding = _uncovered_body_recovery_finding(
            UncoveredBodyRecoveryFindingRequest(
                op_id=rop.op_id,
                source_statute=self.amendment_id,
                target_unit_kind=rop.target_unit_kind,
                target_norm=target_scope.target_norm,
                target_chapter=target_scope.target_chapter,
                target_part=target_scope.target_part,
            )
        )
        if finding is None:
            return
        key = RecoveryFindingKey(
            kind=str(finding.kind or ""),
            target_norm=str(target_scope.target_norm or ""),
            target_chapter=str(target_scope.target_chapter or ""),
            target_part=str(target_scope.target_part or ""),
            op_id=str(rop.op_id or ""),
        )
        if key in self.seen_recovery_findings:
            return
        self.seen_recovery_findings.add(key)
        self.findings_out.append(finding)

    def note_chapter_disposition(self, chapter_label: str, kind: str) -> None:
        """Tally an adopt/own disposition for a chapter-payload section."""
        self.chapter_payload_dispositions.setdefault(
            chapter_label, {"adopted": 0, "owned": 0}
        )[kind] += 1

    def emit_chapter_payload_mixed_findings(self) -> None:
        """Emit a mixed-disposition finding for any chapter with adopt+own splits."""
        if self.findings_out is None:
            return
        for chapter_label, counts in sorted(self.chapter_payload_dispositions.items()):
            adopted_count = counts.get("adopted", 0)
            owned_count = counts.get("owned", 0)
            if adopted_count and owned_count:
                self.findings_out.append(
                    _uncovered_body_chapter_payload_mixed_finding(
                        source_statute=self.amendment_id,
                        target_chapter=chapter_label,
                        adopted_count=adopted_count,
                        owned_count=owned_count,
                    )
                )

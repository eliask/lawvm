"""Uncovered body recovery subsystem, extracted from grafter.py.

This module handles the fallback path where the johtolause parser missed
structural ops in an amendment body, and synthesizes them by scanning the
raw body and applying repeal/insert heuristics.  It corresponds to the
``_recover_uncovered_body_ops`` family of functions that were previously
inlined in grafter.py.

Functions exported:
  _strict_rejected_uncovered_body_finding
  _uncovered_body_recovery_finding
  _recover_uncovered_body_ops_typed
  _apply_uncovered_kumotaan_typed
  _pre_scan_repeal_targets
"""

from __future__ import annotations

import logging
import os
import re
import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Set, Tuple, cast

import lxml.etree as etree

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    OperationSource,
)
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.phase_result import Finding
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core import tree_ops as _tops
from lawvm.core.coverage import CoverageIgnoredUnit, CoverageRejectedClaim, CoverageReport

from lawvm.finland.ops import (
    OpType,
    AmendmentOp,
    ResolvedOp,
    FailedOp,
)
from lawvm.finland.helpers import (
    _norm_num_token,
    _normalize_source_part_num,
    _roman_label_to_arabic,
    _fi_label_postprocessor,
)
from lawvm.finland.body_coverage import (
    extract_body_coverage,
    collect_coverage_claims,
    analyze_coverage,
)
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
from lawvm.finland.johto_scope_mentions import (
    collect_johto_chapter_scope_mentions,
    collect_johto_mentioned_section_labels as _collect_johto_mentioned_section_labels_impl,
    collect_johto_mentioned_section_labels_frozenset as _collect_johto_mentioned_section_labels_frozenset_impl,
    expand_johto_section_label_range as _expand_johto_section_label_range_impl,
)
from lawvm.finland.body_pairing import (
    build_observed_body_inventory,
    build_clause_claims as _bp_build_clause_claims,
    clause_ast_from_amendment_ops as _bp_clause_ast_from_ops,
    assign_body_units_subtree_aware,
    build_chapter_subtree_coverage,
    enforce_pairing_invariants,
    should_use_body_section,
)
from lawvm.finland.restructure_plan import (
    build_restructure_plan,
    RestructureSignal,
    StructuralTransformPlan,
)
from lawvm.finland.replay_notices import replay_verbose_enabled
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.merge import (
    _merge_section_with_omission_ir,
)
from lawvm.finland.apply_ir_ops import (
    _build_repeal_placeholder_ir,
    _relabel_section_ir,
)
from lawvm.finland.kumotaan import (
    _extract_kumotaan_chapter_section_map,
    _extract_kumotaan_container_refs,
)
from lawvm.finland.metadata import (
    _amendment_effective_date,
)
from lawvm.core.payload_elaboration import PayloadCompletenessWitness
from lawvm.finland.acquisition import build_amendment_acquisition_result
from lawvm.finland.vts import VtsSkippedTarget, VtsSourceDiagnostic, extract_voimaantulo_repeals
from lawvm.finland.source_pathology import build_same_effective_container_repeal_shadowed_pathology
from lawvm.finland.johtolause import extract_legal_ops as extract_johtolause_legal_ops
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.uncovered_target_resolve import TargetVerdict, resolve_insert_chapter, resolve_target
from lawvm.finland.uncovered_dispose import (
    ExistingDisposition,
    classify_existing_disposition,
    compute_replace_decision,
    evaluate_omission_merge,
    evaluate_past_repeal_guard,
)
from lawvm.finland.constraints import DEBUG
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.xml_ingest import _tag

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState, StatuteContext
    from lawvm.corpus_store import CorpusStore

logger = logging.getLogger(__name__)

FI_RECOVERY_UNCOVERED_BODY_RULE_ID = "fi.recovery.uncovered_body"
FI_RECOVERY_UNCOVERED_KUMOTAAN_RULE_ID = "fi.recovery.uncovered_kumotaan"
FI_RECOVERY_UNCOVERED_CHAPTER_SCAFFOLD_RULE_ID = "fi.recovery.uncovered_chapter_scaffold"
PRESCAN_REPEAL_TARGET_DIAGNOSTIC_RULE_ID = "PARSE.FUTURE_REPEAL_PRESCAN_DIAGNOSTIC"

PreScanRepealDiagnosticReason = Literal[
    "missing_source",
    "prescan_parse_error",
    "vts_extraction_error",
]


@dataclass(frozen=True, slots=True)
class PreScanRepealDiagnostic:
    """Typed visibility record for future-repeal pre-scan blind spots."""

    rule_id: str
    reason_code: PreScanRepealDiagnosticReason
    source_reason: str
    source_statute: str
    source_excerpt: str = ""
    exception_type: str = ""
    exception_message: str = ""
    phase: str = "frontend_extraction"
    family: str = "future_repeal_prescan"
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: str = "record"

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "reason_code": self.reason_code,
            "source_reason": self.source_reason,
            "source_statute": self.source_statute,
            "source_excerpt": self.source_excerpt,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "phase": self.phase,
            "family": self.family,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


@dataclass(frozen=True, slots=True)
class UncoveredSectionKey:
    """Part/chapter/section key used by uncovered-body replay guards."""

    part: str
    chapter: str
    section: str


@dataclass(frozen=True, slots=True)
class UncoveredSkipKey:
    """Stable de-duplication key for uncovered-body skipped-recovery findings."""

    reason: str
    part: str
    chapter: str
    section: str


@dataclass(frozen=True, slots=True)
class RecoveredSectionKey:
    """Section recovered by uncovered-body synthesis, scoped by chapter."""

    section: str
    chapter: str


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
class CoveredContainerKey:
    """Container already owned by a parsed repeal operation."""

    target_unit_kind: str
    label: str


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
            _uncovered_section_key(part=part, chapter=chapter, section=section)
        )

    def is_covered(
        self,
        *,
        part: str | None,
        chapter: str | None,
        section: str,
    ) -> bool:
        exact_key = _uncovered_section_key(part=part, chapter=chapter, section=section)
        if (
            _uncovered_section_key(part=None, chapter=None, section=section)
            in self.covered_sections
        ):
            return True
        if (
            exact_key.part
            and _uncovered_section_key(part=exact_key.part, chapter=None, section=section)
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
            _uncovered_section_key(part=part, chapter=chapter, section=section)
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
            _uncovered_section_key(part=part, chapter=chapter, section=section)
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
            _uncovered_section_key(part=part, chapter=chapter, section=section)
            in self.chapter_payload_owned_sections
        )


def _uncovered_section_key(
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


@dataclass(frozen=True, slots=True)
class UncoveredCandidateAudit:
    """One typed audit record per uncovered-body candidate decision.

    The observable decision trail for uncovered recovery: every candidate that
    enters ``_process_section_candidate`` produces exactly one of these, naming
    the resolved target and the disposition taken (recover/skip + reason). This
    is the spec-substitute for the recovery compiler — without a source spec,
    the per-candidate verdict trail is how we know what the recovery did and can
    locate where it is wrong.
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
        # A recovered candidate must name the op it produced; a skip must not.
        recovered = self.disposition in ("INSERT", "REPLACE", "MERGE", "ADOPT")
        if recovered and not self.op_id:
            raise ValueError(
                f"recovered disposition {self.disposition!r} requires an op_id"
            )


@dataclass(frozen=True, slots=True)
class UncoveredBodyRecoveryRequest:
    """Semantic inputs for uncovered-body recovery over one amendment body."""

    state: "ReplayState"
    ctx: "StatuteContext"
    ops: List[AmendmentOp]
    muutos_tree: etree._Element
    amendment_id: str
    future_repeals: Optional[Set["RepealTargetRef"]] = None
    op_source: Optional[OperationSource] = None
    new_chapter_labels: Optional[Set[str]] = None


@dataclass(frozen=True, slots=True)
class UncoveredBodyRecoverySinks:
    """Mutable evidence/output channels for uncovered-body recovery."""

    failed_ops_out: Optional[List[FailedOp]] = None
    restructure_plans_out: Optional[List[StructuralTransformPlan]] = None
    observations_out: Optional[List[Dict[str, object]]] = None
    findings_out: Optional[List[Finding]] = None


@dataclass(frozen=True, slots=True)
class UncoveredBodyRecoveryResult:
    """Recovered operations plus the per-candidate audit trail that produced them."""

    recovered_ops: Tuple[ResolvedOp, ...]
    candidate_audits: Tuple[UncoveredCandidateAudit, ...]


@dataclass(frozen=True, slots=True)
class KumotaanRecoveryRequest:
    """Semantic inputs for uncovered ``kumotaan`` recovery."""

    state: "ReplayState"
    ctx: "StatuteContext"
    ops: List[AmendmentOp]
    johto: str
    amendment_id: str
    op_source: Optional[OperationSource] = None


@dataclass(frozen=True, slots=True)
class KumotaanRecoverySinks:
    """Mutable evidence/output channels for uncovered ``kumotaan`` recovery."""

    lo_ops_out: Optional[List[_LegalOperation]] = None
    findings_out: Optional[List[Finding]] = None
    source_pathologies_out: Optional[List[SourcePathology]] = None


@dataclass(frozen=True, slots=True)
class KumotaanRecoveryResult:
    """Result of uncovered ``kumotaan`` recovery."""

    state: "ReplayState"


@dataclass(frozen=True, slots=True)
class PreScanRepealTargetsRequest:
    """Inputs for lightweight future-repeal pre-scan over an amendment schedule."""

    muutoslait: List[str]
    corpus_store: "CorpusStore"
    parent_id: str = ""
    parent_title: str = ""
    cutoff_date: Optional[dt.date] = None


@dataclass(frozen=True, slots=True)
class PreScanRepealTargetsSinks:
    """Diagnostic channels for VTS extraction during future-repeal pre-scan."""

    vts_skipped_targets_out: Optional[List[VtsSkippedTarget]] = None
    vts_source_diagnostics_out: Optional[List[VtsSourceDiagnostic]] = None
    prescan_diagnostics_out: Optional[List[PreScanRepealDiagnostic]] = None


@dataclass(slots=True)
class RecoveryState:
    """Single typed container threading the per-candidate uncovered recovery.

    Consolidates the mutable state previously shared by closure capture across
    ~17 nested functions in ``_recover_uncovered_body_ops`` (the result list,
    de-dup key sets, ownership guards, and chapter-payload disposition counts)
    plus the read-only context the emit/skip methods need (findings sink,
    amendment id, op source). Mutation is confined to the explicit methods
    below, so the recovery's effects are auditable in one place rather than
    scattered across closures.

    The loop lives inside this stateful stage by design: a mid-loop
    ``mark_covered`` is read by later candidates, so the per-candidate decisions
    are genuinely sequential and cannot be a pure batch pipeline.
    """

    # --- read-only context ---
    amendment_id: str
    op_source: Optional[OperationSource]
    findings_out: Optional[List[Finding]]
    guards: UncoveredRecoveryGuards

    # --- mutable accumulators ---
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
        """Record (and de-duplicate) a skipped-recovery finding + audit record."""
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


@dataclass(slots=True)
class KumotaanRecoveryFindingEmitter:
    """Deduplicating finding emitter for uncovered ``kumotaan`` recovery."""

    amendment_id: str
    findings_out: Optional[List[Finding]]
    seen_recovery_findings: Set[KumotaanRecoveryFindingKey] = field(default_factory=set)
    seen_skip_findings: Set[UncoveredSkipKey] = field(default_factory=set)

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


class ChapterPayloadOutcome(Enum):
    """Disposition of a section whose chapter payload is owned by an INSERT op."""

    NOT_APPLICABLE = "not_applicable"  # not chapter-payload-owned → continue to resolution
    ADOPT = "adopt"                    # absent from the new chapter → adopt it as INSERT
    OWNED = "owned"                    # already placed in the new chapter → mark covered
    FUTURE_REPEAL_SKIP = "future_repeal_skip"  # absent but a later amendment repeals it


@dataclass(frozen=True, slots=True)
class ChapterPayloadVerdict:
    """Typed outcome of the chapter-payload ownership phase (pure decision)."""

    outcome: ChapterPayloadOutcome


@dataclass(frozen=True, slots=True)
class ChapterPayloadOwnershipRequest:
    """Inputs for the chapter-payload-owned section disposition decision."""

    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]
    guards: UncoveredRecoveryGuards
    section_present_in_chapter: bool
    future_repealed: bool


def _evaluate_chapter_payload_ownership(
    request: ChapterPayloadOwnershipRequest,
) -> ChapterPayloadVerdict:
    """Decide how a chapter-payload-owned section is disposed of. Pure.

    A whole-chapter INSERT/REPLACE op already owns its child sections; this phase
    decides whether such a section must be explicitly adopted (it was filtered
    from the chapter payload and is still absent from master), is already owned
    (present), or is skipped because a later amendment will repeal it. Returns
    NOT_APPLICABLE when the section is not chapter-payload-owned, so the caller
    proceeds to ordinary target resolution.
    """
    label = request.label
    amend_chapter_label = request.amend_chapter_label
    amend_part_label = request.amend_part_label
    guards = request.guards
    section_present_in_chapter = request.section_present_in_chapter
    future_repealed = request.future_repealed

    if not (
        amend_chapter_label
        and guards.is_chapter_payload_owned(
            part=amend_part_label,
            chapter=amend_chapter_label,
            section=label,
        )
    ):
        return ChapterPayloadVerdict(ChapterPayloadOutcome.NOT_APPLICABLE)
    if section_present_in_chapter:
        return ChapterPayloadVerdict(ChapterPayloadOutcome.OWNED)
    if future_repealed:
        return ChapterPayloadVerdict(ChapterPayloadOutcome.FUTURE_REPEAL_SKIP)
    return ChapterPayloadVerdict(ChapterPayloadOutcome.ADOPT)


@dataclass(frozen=True, slots=True)
class PreGuardVerdict:
    """Outcome of the uncovered-candidate pre-guard filter phase.

    The pre-guards are pure read-only filters that run before target resolution:
    duplicate-already-recovered, moved-destination-mismatch, same-wave relabel
    ownership, and the body-pairing (foreign/unmatched/repeal) guard. The verdict
    says whether the candidate proceeds to resolution, and if not, the typed skip
    reason + part scope to record — so the early-exit decision is one auditable
    value instead of four scattered returns.
    """

    proceed: bool
    skip_reason: Optional[str]
    with_part: bool  # whether the skip finding should carry the part label

    def __post_init__(self) -> None:
        if self.proceed and self.skip_reason is not None:
            raise ValueError("a proceeding pre-guard verdict must not carry a skip reason")
        if not self.proceed and self.skip_reason is None:
            raise ValueError("a blocking pre-guard verdict must name a skip reason")


@dataclass(frozen=True, slots=True)
class PreGuardRequest:
    """Inputs for the uncovered-candidate pre-resolution guard phase."""

    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]
    guards: UncoveredRecoveryGuards
    already_recovered: bool
    moved_section_destinations: Dict[str, str]
    bp_assignments: object


def _evaluate_pre_guards(request: PreGuardRequest) -> PreGuardVerdict:
    """Run the read-only pre-resolution filters and return one typed verdict.

    Pure: reads guard/ownership state and pairing assignments but mutates
    nothing. Filter order is preserved from the legacy cascade (first match
    wins), so the skip a candidate gets is identical to before.
    """
    label = request.label
    amend_chapter_label = request.amend_chapter_label
    amend_part_label = request.amend_part_label
    guards = request.guards
    already_recovered = request.already_recovered
    moved_section_destinations = request.moved_section_destinations
    bp_assignments = request.bp_assignments

    if already_recovered:
        return PreGuardVerdict(False, "duplicate_recovered_candidate", with_part=False)

    move_destination = moved_section_destinations.get(label)
    if move_destination and amend_chapter_label != move_destination:
        return PreGuardVerdict(False, "moved_destination_mismatch", with_part=False)

    if amend_chapter_label and guards.is_relabel_destination(
        part=amend_part_label,
        chapter=amend_chapter_label,
        section=label,
    ):
        return PreGuardVerdict(False, "same_wave_relabel_destination_owned", with_part=True)

    if bp_assignments and not should_use_body_section(
        label, amend_chapter_label or "", cast("list", bp_assignments)
    ):
        return PreGuardVerdict(False, "body_pairing_guard", with_part=False)

    return PreGuardVerdict(True, None, with_part=False)


@dataclass(frozen=True, slots=True)
class UncoveredRopDraft:
    """Draft fields for one synthetic uncovered-body section operation."""

    op_type: OpType
    target_label: str
    target_chapter: Optional[str]
    target_part: Optional[str]
    muutos_ir: IRNode
    op_id: str


@dataclass(frozen=True, slots=True)
class UncoveredChapterScaffoldDraft:
    """Draft fields for a synthetic chapter LegalOperation recovery."""

    op_id: str
    path: tuple[tuple[str, str], ...]
    payload: IRNode
    source: Optional[OperationSource]
    amendment_id: str


@dataclass(frozen=True, slots=True)
class ExistingSectionCandidate:
    """Live section candidate resolved for uncovered-body recovery."""

    existing: IRNode
    existing_path: tuple[tuple[str, str], ...]
    sec_ir: IRNode
    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]
    cross_chapter: bool


@dataclass(frozen=True, slots=True)
class NewSectionCandidate:
    """Uncovered-body section candidate with no resolvable live target."""

    sec_ir: IRNode
    label: str
    amend_chapter_label: Optional[str]
    amend_part_label: Optional[str]


def _build_uncovered_rop(
    draft: UncoveredRopDraft,
    *,
    amendment_id: str,
    op_source: Optional[OperationSource],
) -> ResolvedOp:
    """Build a ResolvedOp for an uncovered-body section operation.

    Pure constructor: assembles the synthetic AmendmentOp + ResolvedOp for a
    section recovered from the amendment body, stamping the whole-section
    completeness witness and the part/chapter/section target address. No state
    capture — every input is an explicit argument so the synthesized op is
    reproducible from the audit record alone.
    """
    am_op = AmendmentOp(
        op_id=draft.op_id,
        op_type=draft.op_type,
        target_section=draft.target_label,
        target_unit_kind="section",
        target_chapter=draft.target_chapter,
        target_part=draft.target_part,
        source_statute=amendment_id,
        uncovered_body_recovery=True,
        witness_rule_id=FI_RECOVERY_UNCOVERED_BODY_RULE_ID,
    )
    return ResolvedOp.from_amendment_op(
        am_op,
        muutos_ir=draft.muutos_ir,
        cross_ir=None,
        target_unit_kind="section",
        target_norm=draft.target_label,
        target_chapter=draft.target_chapter,
        payload_completeness=_uncovered_section_payload_completeness(
            op_type=draft.op_type,
            muutos_ir=draft.muutos_ir,
        ),
        op_source=op_source,
        target_address=LegalAddress(
            path=(
                ((("part", draft.target_part),) if draft.target_part else ())
                + ((("chapter", draft.target_chapter),) if draft.target_chapter else ())
                + (("section", draft.target_label),)
            )
        ),
    )


def build_uncovered_chapter_scaffold_lo(draft: UncoveredChapterScaffoldDraft) -> _LegalOperation:
    """Build a chapter-scaffold LegalOperation with explicit recovery witness.

    Uncovered-body replay sometimes has to materialize a chapter container before
    section-level recovered ops can attach to it.  That scaffold is still legal
    state, so it must carry the same rule-attribution surface as recovered
    section ops instead of being an anonymous LO side effect.
    """
    return _LegalOperation(
        op_id=draft.op_id,
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=draft.path),
        payload=draft.payload,
        source=draft.source,
        group_id=f"finland-johto:{draft.amendment_id}",
        witness_rule_id=FI_RECOVERY_UNCOVERED_CHAPTER_SCAFFOLD_RULE_ID,
    )


def _uncovered_disposition_for_op_id(op_id: str) -> tuple[str, str]:
    """Map a recovered op_id to its (disposition, reason) audit pair.

    The op_id prefix carries the disposition the cascade took; decode it once
    here so the audit record names the action without each call site repeating
    it. Falls back to a generic INSERT label for unrecognized prefixes.
    """
    if op_id.startswith("uncov_chapter_adopt_"):
        return "ADOPT", "chapter_payload_adopt"
    if op_id.startswith("uncovered_replace_"):
        return "REPLACE", "replace_existing"
    if op_id.startswith("uncovered_merge_"):
        return "MERGE", "omission_merge"
    if op_id.startswith("uncovered_insert_"):
        return "INSERT", "new_insert"
    return "INSERT", "recovered"


def _section_heading_text(node: IRNode) -> str:
    """Normalized lowercase heading text of a section IR node (or "")."""
    heading = next((c for c in node.children if c.kind is IRNodeKind.HEADING), None)
    return " ".join(irnode_to_text(heading).split()).strip().lower() if heading is not None else ""


def _next_letter_label(label: str) -> Optional[str]:
    """Next letter-suffixed sibling label (e.g. ``18`` → ``18a``, ``18a`` → ``18b``)."""
    norm = _norm_num_token(label)
    m = re.fullmatch(r"(\d+)([a-z]?)", norm)
    if not m:
        return None
    base, suffix = m.groups()
    if not suffix:
        return f"{base}a"
    if suffix == "z":
        return None
    return f"{base}{chr(ord(suffix) + 1)}"


def _xml_part_label(section_el: etree._Element) -> Optional[str]:
    """Normalized part label of the nearest <part> ancestor of a section element."""
    parent = section_el.getparent()
    while parent is not None:
        if _tag(parent) == "part":
            num_el = parent.find("{*}num")
            if num_el is not None and num_el.text:
                return _normalize_source_part_num(num_el.text) or None
        parent = parent.getparent()
    return None


def _part_label_from_path(path: tuple[tuple[str, str], ...] | None) -> Optional[str]:
    """First part label in a resolved provision path, if any."""
    if not path:
        return None
    return next((lbl for kind, lbl in path if kind == "part"), None)


def _uncovered_section_payload_completeness(
    *,
    op_type: OpType,
    muutos_ir: IRNode,
) -> PayloadCompletenessWitness | None:
    """Classify uncovered section-root payload ownership for replay tail masking.

    Uncovered-body recovery synthesizes full section INSERT/REPLACE ops directly
    from body XML, bypassing the normal payload-normalization path that stamps a
    tail policy onto section roots. Whole-section REPLACEs must therefore carry
    an explicit completeness witness; otherwise PIT materialization may preserve
    stale descendant timelines under the newer section root.
    """
    if muutos_ir.kind is not IRNodeKind.SECTION:
        return None
    if op_type != "REPLACE":
        return None
    return PayloadCompletenessWitness(
        kind="complete",
        reasons=("uncovered_whole_section_replace",),
        tail_policy="replace_if_target_scope_requires",
    )


def _strict_rejected_uncovered_body_finding(
    *,
    source_statute: str,
    stage: str,
) -> Finding:
    return _strict_rejected_uncovered_body_finding_impl(
        source_statute=source_statute,
        stage=stage,
    )


@dataclass(frozen=True, slots=True)
class UncoveredBodyRecoveryFindingRequest:
    """Evidence fields for an uncovered-body recovery obligation finding."""

    op_id: str
    source_statute: str
    target_unit_kind: str
    target_norm: str
    target_chapter: str | None = None
    target_part: str | None = None


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
    """Backward-compat wrapper for body_coverage_findings."""
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
    """Backward-compat wrapper for body_coverage_findings."""
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
    """Backward-compat wrapper for body_coverage_findings."""
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
    """Backward-compat wrapper for body_coverage_findings."""
    return _coverage_unresolved_gap_finding_impl(
        source_statute=request.source_statute,
        disposition=request.disposition,
        unit_kind=request.unit_kind,
        observed_label=request.observed_label,
        parent_label=request.parent_label,
        evidence=request.evidence,
    )


def _expand_johto_section_label_range(start: str, end: str) -> tuple[str, ...]:
    return _expand_johto_section_label_range_impl(start, end)


def _collect_johto_mentioned_section_labels(johto_text: str) -> set[str]:
    return _collect_johto_mentioned_section_labels_impl(johto_text)


def _collect_johto_mentioned_section_labels_frozenset(johto_text: str) -> frozenset[str]:
    return _collect_johto_mentioned_section_labels_frozenset_impl(johto_text)


def _build_peg_covered_sets(
    ops: List[AmendmentOp],
    failed_ops_out: Optional[List[FailedOp]],
) -> set[UncoveredSectionKey]:
    """Section labels already covered by PEG ops (uncovered-recovery input).

    Pure: derives the part/chapter-aware "already covered" label set from the
    compiled PEG ops, excluding ops that FAILED during apply (a failed op blocks
    recovery but did not modify the tree, so the body fallback should still
    apply).

    The set is part+chapter-aware: an op with chapter="" covers the section in
    all chapters within the same part; a truly unscoped op uses part=""/chapter=""
    as the global wildcard. The returned set is aliased by the caller into the
    recovery guard object, so its identity matters.
    """
    failed_sections: Set[str] = set()
    if failed_ops_out:
        for fop in failed_ops_out:
            if fop.target_unit_kind == "section" and fop.target_section:
                failed_sections.add(_norm_num_token(fop.target_section))
    covered_labels: set[UncoveredSectionKey] = set()
    for op in ops:
        if op.target_unit_kind == "section" and op.target_section:
            label = _norm_num_token(op.target_section)
            if label not in failed_sections:
                covered_labels.add(
                    _uncovered_section_key(
                        part=op.target_part,
                        chapter=op.target_chapter,
                        section=label,
                    )
                )
    return covered_labels


def _compute_has_content_ops(ops: List[AmendmentOp], muutos_tree: etree._Element) -> bool:
    """Whether REPLACE/INSERT body recovery is permitted for this amendment.

    True when the PEG ops carry a section-level REPLACE/INSERT. Relaxed to True
    when there are chapter-level REPLACE/INSERT ops, or when the johtolause
    preamble explicitly says muutetaan/lisätään (PEG truncation: parsed kumotaan
    but missed the muutetaan clause). Downstream omission + subsection guards
    still prevent unsafe replacements.
    """
    if any(op.op_type in ("REPLACE", "INSERT") and op.target_unit_kind == "section" for op in ops):
        return True
    if any(op.op_type in ("REPLACE", "INSERT") and op.target_unit_kind == "chapter" for op in ops):
        return True
    johto_el = muutos_tree.find(".//{*}preamble")
    if johto_el is not None:
        johto_text = etree.tostring(johto_el, method="text", encoding="unicode")
        if re.search(r"\bmuutetaan\b|\blisätään\b", johto_text, re.IGNORECASE):
            return True
    return False


def _emit_coverage_analysis_findings(
    cov_report: CoverageReport,
    findings_out: Optional[List[Finding]],
    amendment_id: str,
) -> None:
    """Emit ignored-unit, rejected-claim, and unresolved-gap findings from a
    coverage report. No-op when ``findings_out`` is None."""
    if findings_out is None:
        return
    for ignored in cov_report.ignored_units:
        findings_out.append(
            _coverage_ignored_unit_finding(
                CoverageIgnoredUnitFindingRequest(
                    source_statute=amendment_id,
                    unit_kind=ignored.unit_kind,
                    reason=ignored.reason,
                    observed_label=ignored.observed_label,
                    parent_label=ignored.parent_label,
                    evidence=ignored.evidence,
                )
            )
        )
    for rejected in cov_report.rejected_claims:
        findings_out.append(
            _coverage_rejected_claim_finding(
                source_statute=amendment_id,
                reason=rejected.reason,
                evidence=rejected.evidence,
            )
        )
    for gap in cov_report.obligations:
        findings_out.append(
            _coverage_unresolved_gap_finding(
                CoverageUnresolvedGapFindingRequest(
                    source_statute=amendment_id,
                    disposition=gap.disposition,
                    unit_kind=gap.unit.kind,
                    observed_label=gap.unit.observed_label,
                    parent_label=gap.unit.parent_label,
                    evidence=gap.evidence,
                )
            )
        )


@dataclass(frozen=True, slots=True)
class HighUncoveredDegradationRequest:
    """Inputs for high-uncovered-body degradation evidence emission."""

    restructure_plan: StructuralTransformPlan
    amendment_id: str
    uncovered_count: int
    total_units: int
    uncov_ratio: float


@dataclass(frozen=True, slots=True)
class HighUncoveredDegradationSinks:
    """Mutable evidence channels for high-uncovered-body degradation."""

    observations_out: Optional[List[Dict[str, object]]] = None
    findings_out: Optional[List[Finding]] = None


def _emit_high_uncovered_degradation(
    request: HighUncoveredDegradationRequest,
    sinks: Optional[HighUncoveredDegradationSinks] = None,
) -> None:
    """Surface a degradation observation/finding when a chapter-level INSERT plan
    still has a high uncovered-body ratio — making the gap explicit instead of
    silently proceeding via permissive fallback. No-op unless both the
    chapter-insert and high-uncovered signals are present and observations_out
    is provided.

    ``uncovered_count`` is the restructure (container-inclusive) uncovered tally
    that drives the plan, not the container-excluded reported metric — the
    degradation numbers must track the ratio that built the plan.
    """
    restructure_plan = request.restructure_plan
    amendment_id = request.amendment_id
    uncovered_count = request.uncovered_count
    total_units = request.total_units
    uncov_ratio = request.uncov_ratio
    observations_out = sinks.observations_out if sinks is not None else None
    findings_out = sinks.findings_out if sinks is not None else None

    has_chapter_insert = RestructureSignal.CHAPTER_INSERT in restructure_plan.signals
    has_high_uncov = RestructureSignal.HIGH_UNCOVERED_BODY in restructure_plan.signals
    if not (has_chapter_insert and has_high_uncov and observations_out is not None):
        return
    signals = [s.value for s in restructure_plan.signals]
    observations_out.append({
        "kind": "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
        "stage": "coverage_analysis",
        "amendment_id": amendment_id,
        "uncovered_count": uncovered_count,
        "total_units": total_units,
        "uncov_ratio": round(uncov_ratio, 4),
        "confidence": restructure_plan.confidence,
        "signals": signals,
    })
    if findings_out is not None:
        findings_out.append(
            _high_uncovered_body_degraded_finding(
                HighUncoveredBodyDegradedFindingRequest(
                    source_statute=amendment_id,
                    uncovered_count=uncovered_count,
                    total_units=total_units,
                    uncov_ratio=uncov_ratio,
                    confidence=restructure_plan.confidence,
                    signals=signals,
                )
            )
        )
    if replay_verbose_enabled():
        logger.warning(
            "  [%s] COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED: "
            "%d/%d units uncovered (ratio=%.2f, confidence=%.2f) — "
            "chapter-level INSERT plan proceeding with degraded confidence",
            amendment_id, uncovered_count, total_units,
            uncov_ratio, restructure_plan.confidence,
        )


@dataclass(slots=True)
class _UncoveredRecoveryRun:
    """Sequential uncovered-body recovery stage.

    The candidate loop is intentionally stateful: each recovered or covered
    section affects the guards seen by later body sections.  Keeping that loop
    in one object makes the live state, johto gates, pairing verdicts, and
    recovery ledger explicit instead of relying on closure capture.
    """

    state: "ReplayState"
    ops: List[AmendmentOp]
    amendment_id: str
    future_repeals: Optional[Set["RepealTargetRef"]]
    new_chapter_labels: Optional[Set[str]]
    has_content_ops: bool
    rstate: RecoveryState
    recovery_guards: UncoveredRecoveryGuards
    bp_assignments: object
    johto_mentioned_labels: Set[str]
    johto_mentioned_replaced_chapters: Set[str]
    moved_section_destinations: Dict[str, str]
    owned_chapter_labels: Set[str]

    def record_skip(
        self,
        reason: str,
        label: str,
        amend_chapter_label: Optional[str],
        amend_part_label: Optional[str] = None,
    ) -> None:
        self.rstate.record_skip(reason, label, amend_chapter_label, amend_part_label)

    def is_future_repealed(self, label: str, chapter: Optional[str]) -> bool:
        """Whether a later amendment explicitly repeals this section.

        Whole-chapter future repeals are intentionally ignored: skipping all
        section inserts would leave an empty chapter and can make the later
        chapter REPEAL fail before it has a target.
        """
        if self.future_repeals is None:
            return False
        if RepealTargetRef.section(label) in self.future_repeals:
            return True
        if chapter and RepealTargetRef.section(label, chapter) in self.future_repeals:
            return True
        return False

    def label_allowed_by_johto(self, label: str, chapter: Optional[str] = None) -> bool:
        if not self.johto_mentioned_labels:
            return True
        if chapter and chapter in self.owned_chapter_labels:
            return True
        if chapter and chapter in self.johto_mentioned_replaced_chapters:
            return True
        if label in self.johto_mentioned_labels:
            return True
        base_label = re.match(r"^(\d+)", label)
        return bool(base_label and base_label.group(1) in self.johto_mentioned_labels)

    def make_uncovered_rop(self, draft: UncoveredRopDraft) -> ResolvedOp:
        return _build_uncovered_rop(
            draft,
            amendment_id=self.amendment_id,
            op_source=self.rstate.op_source,
        )

    def append_recovered_rop(self, rop: ResolvedOp) -> None:
        disposition, reason = _uncovered_disposition_for_op_id(rop.op_id or "")
        self.rstate.append_recovered_rop(rop, disposition=disposition, reason=reason)

    def process_section_candidate(
        self,
        sec: etree._Element,
        label: str,
        amend_chapter_label: Optional[str],
    ) -> None:
        """Process one uncovered section candidate and commit a typed disposition."""
        debug_recovery = os.environ.get("LAWVM_DEBUG_RECOVERY") == "1"
        if debug_recovery:
            print(
                f"  [DBG] _process_section_candidate: "
                f"label={label!r}, chapter={amend_chapter_label!r}"
            )

        amend_part_label = _xml_part_label(sec)
        pre = _evaluate_pre_guards(
            PreGuardRequest(
                label=label,
                amend_chapter_label=amend_chapter_label,
                amend_part_label=amend_part_label,
                guards=self.recovery_guards,
                already_recovered=self.rstate.already_recovered(
                    section=label, chapter=amend_chapter_label
                ),
                moved_section_destinations=self.moved_section_destinations,
                bp_assignments=self.bp_assignments,
            )
        )
        if not pre.proceed:
            assert pre.skip_reason is not None
            if debug_recovery:
                print(
                    f"  [DBG]  -> SKIP ({pre.skip_reason}): "
                    f"{label!r} in chapter {amend_chapter_label!r}"
                )
            if pre.skip_reason == "body_pairing_guard":
                logger.debug(
                    "  [%s] uncovered SKIP %s § — body-pairing guard (foreign/unmatched/repeal)",
                    self.amendment_id,
                    label,
                )
            self.record_skip(
                pre.skip_reason,
                label,
                amend_chapter_label,
                amend_part_label if pre.with_part else None,
            )
            return

        payload = _evaluate_chapter_payload_ownership(
            ChapterPayloadOwnershipRequest(
                label=label,
                amend_chapter_label=amend_chapter_label,
                amend_part_label=amend_part_label,
                guards=self.recovery_guards,
                section_present_in_chapter=(
                    bool(amend_chapter_label)
                    and self.state.find_section_path(label, amend_chapter_label, amend_part_label)
                    is not None
                ),
                future_repealed=self.is_future_repealed(label, amend_chapter_label),
            )
        )
        if payload.outcome is not ChapterPayloadOutcome.NOT_APPLICABLE:
            assert amend_chapter_label is not None
            if debug_recovery:
                print(
                    f"  [DBG]  -> chapter-payload {payload.outcome.value}: section "
                    f"{label!r} in chapter {amend_chapter_label!r}"
                )
            if payload.outcome is ChapterPayloadOutcome.ADOPT:
                adopt_sec_ir = fi_xml_to_ir_node(sec, _fi_label_postprocessor)
                self.rstate.mark_covered(
                    part=amend_part_label,
                    chapter=amend_chapter_label,
                    section=label,
                )
                self.append_recovered_rop(
                    self.make_uncovered_rop(
                        UncoveredRopDraft(
                            op_type="INSERT",
                            target_label=label,
                            target_chapter=amend_chapter_label,
                            target_part=amend_part_label,
                            muutos_ir=adopt_sec_ir,
                            op_id=f"uncov_chapter_adopt_{label}",
                        )
                    )
                )
                self.rstate.note_chapter_disposition(amend_chapter_label, "adopted")
            elif payload.outcome is ChapterPayloadOutcome.OWNED:
                self.rstate.mark_covered(
                    part=amend_part_label,
                    chapter=amend_chapter_label,
                    section=label,
                )
                self.rstate.note_chapter_disposition(amend_chapter_label, "owned")
                self.record_skip("chapter_payload_owned", label, amend_chapter_label)
            else:
                self.record_skip("future_repeal", label, amend_chapter_label)
            return

        resolved = resolve_target(
            label,
            amend_chapter_label,
            amend_part_label,
            self.state,
            self.owned_chapter_labels,
        )
        if resolved.verdict is TargetVerdict.AMBIGUOUS:
            self.record_skip("ambiguous_duplicate_label_no_chapter", label, amend_chapter_label)
            return

        sec_ir = fi_xml_to_ir_node(sec, _fi_label_postprocessor)
        if resolved.existing_path is not None:
            existing = _tops.resolve(self.state.ir, resolved.existing_path)
            if existing is not None:
                self.process_existing_section(
                    ExistingSectionCandidate(
                        existing=existing,
                        existing_path=resolved.existing_path,
                        sec_ir=sec_ir,
                        label=label,
                        amend_chapter_label=amend_chapter_label,
                        amend_part_label=amend_part_label,
                        cross_chapter=resolved.cross_chapter,
                    )
                )
                return

        self.process_new_section(
            NewSectionCandidate(
                sec_ir=sec_ir,
                label=label,
                amend_chapter_label=amend_chapter_label,
                amend_part_label=amend_part_label,
            )
        )

    def process_existing_section(self, candidate: ExistingSectionCandidate) -> None:
        """Commit the disposition for a candidate with a resolvable live section."""
        existing = candidate.existing
        existing_path = candidate.existing_path
        sec_ir = candidate.sec_ir
        label = candidate.label
        amend_chapter_label = candidate.amend_chapter_label
        amend_part_label = candidate.amend_part_label
        cross_chapter = candidate.cross_chapter
        existing_heading = _section_heading_text(existing)
        amend_heading = _section_heading_text(sec_ir)
        if (
            existing_heading.startswith("voimaantulo")
            and amend_heading
            and not amend_heading.startswith("voimaantulo")
        ):
            parent_path = existing_path[:-1]
            parent = _tops.resolve(self.state.ir, parent_path) if parent_path else self.state.ir
            section_siblings = (
                [c for c in parent.children if c.kind is IRNodeKind.SECTION]
                if parent is not None
                else []
            )
            insert_label: Optional[str] = None
            if existing in section_siblings:
                existing_idx = section_siblings.index(existing)
                if existing_idx > 0:
                    insert_label = _next_letter_label(section_siblings[existing_idx - 1].label or "")
            if (
                insert_label
                and self.state.find_section_path(insert_label, amend_chapter_label) is None
            ):
                inserted_sec = _relabel_section_ir(sec_ir, insert_label)
                self.recovery_guards.mark_covered(
                    part=amend_part_label,
                    chapter=amend_chapter_label,
                    section=label,
                )
                self.append_recovered_rop(
                    self.make_uncovered_rop(
                        UncoveredRopDraft(
                            op_type="INSERT",
                            target_label=insert_label,
                            target_chapter=amend_chapter_label,
                            target_part=amend_part_label or _part_label_from_path(existing_path),
                            muutos_ir=inserted_sec,
                            op_id=f"uncovered_insert_{insert_label}",
                        )
                    )
                )
                return

        if not self.label_allowed_by_johto(label, amend_chapter_label):
            self.record_skip("johto_guard", label, amend_chapter_label)
            return

        whole_ch_replace = bool(
            amend_chapter_label
            and amend_chapter_label in self.johto_mentioned_replaced_chapters
        )
        prv = evaluate_past_repeal_guard(
            existing.attrs, self.ops, label, amend_chapter_label, whole_ch_replace
        )
        if prv.applies and not prv.bypass:
            self.recovery_guards.mark_covered(
                part=amend_part_label,
                chapter=amend_chapter_label,
                section=label,
            )
            self.record_skip("past_repeal_placeholder_guard", label, amend_chapter_label)
            return
        if prv.applies:
            logger.debug(
                "  [%s] uncovered: bypassing past-repeal guard for %s § (%s)",
                self.amendment_id,
                label,
                prv.bypass_reason,
            )

        rdec = compute_replace_decision(
            sec_ir, existing, self.has_content_ops, cross_chapter, whole_ch_replace
        )
        edisp = classify_existing_disposition(
            sec_ir, rdec, self.has_content_ops, cross_chapter
        )
        if os.environ.get("LAWVM_DEBUG_RECOVERY") == "1":
            print(
                f"  [DBG]  existing disposition={edisp.outcome.value}, "
                f"has_content_ops={self.has_content_ops}, has_omissions={rdec.has_omissions}, "
                f"cross_chapter={cross_chapter}, would_lose={rdec.would_lose_subsections}, "
                f"whole_ch_replace={whole_ch_replace}, amend_ss={rdec.amend_subsec_count}, "
                f"master_ss={rdec.master_subsec_count}"
            )

        self.recovery_guards.mark_covered(
            part=amend_part_label,
            chapter=amend_chapter_label,
            section=label,
        )
        if edisp.outcome is ExistingDisposition.REPLACE:
            self.append_recovered_rop(
                self.make_uncovered_rop(
                    UncoveredRopDraft(
                        op_type="REPLACE",
                        target_label=label,
                        target_chapter=amend_chapter_label,
                        target_part=amend_part_label or _part_label_from_path(existing_path),
                        muutos_ir=sec_ir,
                        op_id=f"uncovered_replace_{label}",
                    )
                )
            )
        elif edisp.outcome is ExistingDisposition.MERGE_CANDIDATE:
            merged = _merge_section_with_omission_ir(existing, sec_ir)
            if merged is not None:
                mdec = evaluate_omission_merge(merged, existing)
                if mdec.accept:
                    self.append_recovered_rop(
                        self.make_uncovered_rop(
                            UncoveredRopDraft(
                                op_type="REPLACE",
                                target_label=label,
                                target_chapter=amend_chapter_label,
                                target_part=amend_part_label or _part_label_from_path(existing_path),
                                muutos_ir=merged,
                                op_id=f"uncovered_merge_{label}",
                            )
                        )
                    )
                elif mdec.skip_reason is not None:
                    self.record_skip(f"omission_merge_{mdec.skip_reason}", label, amend_chapter_label)
            else:
                self.record_skip("omission_merge_failed", label, amend_chapter_label)
        elif (
            edisp.skip_reason is not None
            and edisp.outcome is not ExistingDisposition.SKIP_BLOCKED
        ):
            self.record_skip(edisp.skip_reason, label, amend_chapter_label)

    def process_new_section(self, candidate: NewSectionCandidate) -> None:
        """Commit the disposition for a candidate without a live target."""
        sec_ir = candidate.sec_ir
        label = candidate.label
        amend_chapter_label = candidate.amend_chapter_label
        amend_part_label = candidate.amend_part_label
        if not self.label_allowed_by_johto(label, amend_chapter_label):
            self.record_skip("johto_guard", label, amend_chapter_label)
            return

        if self.is_future_repealed(label, amend_chapter_label):
            if DEBUG:
                _replay_print(
                    f"  [{self.amendment_id}] uncovered SKIP INSERT {label} § — future repeal"
                )
            self.recovery_guards.mark_covered(
                part=amend_part_label,
                chapter=amend_chapter_label,
                section=label,
            )
            self.record_skip("future_repeal", label, amend_chapter_label)
            return

        insert_ch = resolve_insert_chapter(
            label,
            amend_chapter_label,
            amend_part_label,
            self.state,
            self.ops,
            self.new_chapter_labels,
            self.owned_chapter_labels,
        )
        effective_chapter = insert_ch.effective_chapter
        effective_part = insert_ch.effective_part
        if insert_ch.reason == "family_base_override":
            logger.debug(
                "  [%s] uncovered INSERT %s: overriding chapter %s→%s"
                " (family base in unrelated existing chapter)",
                self.amendment_id,
                label,
                amend_chapter_label,
                effective_chapter,
            )

        self.recovery_guards.mark_covered(
            part=amend_part_label,
            chapter=effective_chapter,
            section=label,
        )
        self.append_recovered_rop(
            self.make_uncovered_rop(
                UncoveredRopDraft(
                    op_type="INSERT",
                    target_label=label,
                    target_chapter=effective_chapter,
                    target_part=effective_part,
                    muutos_ir=sec_ir,
                    op_id=f"uncovered_insert_{label}",
                )
            )
        )


def _recover_uncovered_body_ops_typed(
    request: UncoveredBodyRecoveryRequest,
    sinks: Optional[UncoveredBodyRecoverySinks] = None,
) -> UncoveredBodyRecoveryResult:
    """Collect body-driven ResolvedOps for sections not covered by PEG ops.

    MVR (minimum viable refactor): this function now RETURNS a list of
    ResolvedOp objects instead of mutating the tree directly.  The caller
    feeds them through the normal apply_op path so that the ResolvedOp
    boundary is respected.

    ``state`` is used READ-ONLY for target lookups (find_section_path,
    provision_index, etc.).  No tree mutations happen here.

    ``future_repeals`` is an optional set of typed repeal-target refs that
    will be repealed by *later*
    amendments in the schedule.  When a candidate section for uncovered-body
    insertion is already targeted by a later REPEAL the insert is suppressed —
    the section will be removed by that later amendment anyway, so inserting it
    now would only introduce a spurious intermediate state that the oracle never
    shows.

    Note: chapter pre-creation is a separate pre-step (_pre_create_amendment_chapters)
    and must be called before this function.
    """
    sinks = sinks or UncoveredBodyRecoverySinks()
    state = request.state
    ctx = request.ctx
    ops = request.ops
    muutos_tree = request.muutos_tree
    amendment_id = request.amendment_id
    future_repeals = request.future_repeals
    op_source = request.op_source
    new_chapter_labels = request.new_chapter_labels
    failed_ops_out = sinks.failed_ops_out
    restructure_plans_out = sinks.restructure_plans_out
    observations_out = sinks.observations_out
    findings_out = sinks.findings_out

    # PEG-covered guard sets (see _build_peg_covered_sets). covered_labels is
    # aliased into recovery_guards below, so its identity must be preserved.
    covered_labels = _build_peg_covered_sets(ops, failed_ops_out)

    # --- Typed coverage analysis (primary source for uncovered sections) ---
    # Coverage analysis replaces the ad-hoc per-section scan as the primary
    # detector.  extract_body_coverage already classifies nonoperative/provenance
    # sections via tags, so the existing noise-filtering heuristics are
    # handled before we even enter the loop below.
    _ignored_units: list[CoverageIgnoredUnit] = []
    _rejected_claims: list[CoverageRejectedClaim] = []
    _cov_units = extract_body_coverage(muutos_tree, ignored_units_out=_ignored_units)
    _cov_claims = collect_coverage_claims(ops, rejected_claims_out=_rejected_claims)
    _cov_report = analyze_coverage(
        _cov_units,
        _cov_claims,
        ignored_units=_ignored_units,
        rejected_claims=_rejected_claims,
    )
    _emit_coverage_analysis_findings(_cov_report, findings_out, amendment_id)
    muutos_body = muutos_tree.find(".//{*}body")
    if muutos_body is None:
        return UncoveredBodyRecoveryResult(recovered_ops=(), candidate_audits=())
    # Container-only chapters (scoping wrappers around section edits) are not
    # operative units — ops claim sections, not the wrapper — so they are
    # excluded from the REPORTED coverage metric, where counting them makes
    # claimed<units a spurious "dropped op?" signal. Restructure detection below
    # must stay byte-identical, so it keeps the historical container-inclusive
    # uncovered count via _restructure_uncov_count.
    _container_chapter_gaps = sum(
        1
        for _g in _cov_report.gaps
        if "container" in _g.unit.tags
        and _g.unit.kind == "chapter"
        and _g.disposition == "covered_by_broad_scope"
    )
    _restructure_uncov_count = _cov_report.uncovered_count + _container_chapter_gaps
    if _cov_report.uncovered_count > 0:
        _operative_unit_count = sum(
            1
            for _u in _cov_units
            if not ("container" in _u.tags and _u.kind == "chapter")
        )
        _replay_print(
            f"  [{amendment_id}] Coverage: {_operative_unit_count} units, "
            f"{len(_cov_claims)} claimed, "
            f"{_cov_report.uncovered_count} uncovered"
        )
    # --- Restructure signal detection + StructuralTransformPlan ---
    # Detect large-restructure amendments: chapter/part inserts + high uncovered ratio.
    # When signals are present, build a typed plan for auditing and future execution.
    #
    # Restructure detection deliberately keeps the historical uncovered count,
    # which treated unmatched container-only chapters as uncovered. The
    # uncov_ratio that drives plan building — which mutates the replay tree —
    # must stay byte-identical, so it uses _restructure_uncov_count, not the
    # container-excluded reported count.
    _total_units = len(_cov_units)
    _uncov_ratio = _restructure_uncov_count / _total_units if _total_units > 0 else 0.0

    # --- Body pairing analysis (guards foreign/unmatched body use) ---
    _bp_inventory = build_observed_body_inventory(muutos_tree)
    _bp_ast = _bp_clause_ast_from_ops(ops)
    _bp_claims = _bp_build_clause_claims(_bp_ast, ctx.id)
    # Use subtree-aware assignment: chapter INSERT ops implicitly claim their
    # child sections in the amendment body, so those sections are not spuriously
    # flagged as "unmatched" when no per-section PEG op exists for them.
    _bp_assignments = assign_body_units_subtree_aware(_bp_inventory, _bp_claims, ctx.id)
    _bp_findings = enforce_pairing_invariants(_bp_assignments, ctx.id, amendment_id)
    if _bp_findings:
        for _bpf in _bp_findings:
            logger.debug("  [%s] body-pairing: %s: %s", amendment_id, _bpf.kind, _bpf.detail)
    _bp_inventory_by_id = {unit.unit_id: unit for unit in _bp_inventory}
    chapter_payload_owned_sections: set[UncoveredSectionKey] = set()
    for _assignment in _bp_assignments:
        if _assignment.status != "claimed_current" or _assignment.claim is None:
            continue
        _unit = _bp_inventory_by_id.get(_assignment.body_unit_id)
        if _unit is None or _unit.kind != "section" or not _unit.chapter_label:
            continue
        _claim = _assignment.claim
        if (
            _claim.target_statute == ctx.id
            and _claim.claim_kind == "INSERT"
            and _claim.chapter == ""
            and _claim.target_address == _unit.chapter_label
        ):
            chapter_payload_owned_sections.add(
                _uncovered_section_key(
                    part=_unit.part_label,
                    chapter=_unit.chapter_label,
                    section=_unit.label,
                )
            )
    # --- end body pairing analysis ---

    # Build body_unit_ids_by_chapter for subtree-aware plan building.
    # Prefer build_chapter_subtree_coverage (chapter INSERT-scoped) for plan
    # subtree claims; fall back to raw chapter grouping from inventory.
    _chapter_subtree_coverage = build_chapter_subtree_coverage(_bp_inventory, _bp_claims, ctx.id)
    _body_unit_ids_by_chapter: dict[tuple[str, str], list[str]] = dict(_chapter_subtree_coverage)
    # Also add chapter groupings not covered by INSERT claims (for the full plan)
    for _bpu in _bp_inventory:
        if _bpu.kind == "section" and _bpu.chapter_label:
            _chapter_key = (_bpu.part_label, _bpu.chapter_label)
            if _chapter_key not in _body_unit_ids_by_chapter:
                _body_unit_ids_by_chapter.setdefault(_chapter_key, []).append(_bpu.unit_id)

    _restructure_plan: Optional[StructuralTransformPlan] = build_restructure_plan(
        ctx.id,
        amendment_id,
        ops=list(ops),
        uncov_ratio=_uncov_ratio,
        total_units=_total_units,
        body_unit_ids_by_chapter=_body_unit_ids_by_chapter,
    )
    if _restructure_plan is not None:
        logger.info(
            "  [%s] StructuralTransformPlan built: signals=%s, ops=%d, confidence=%.2f",
            amendment_id,
            [s.value for s in _restructure_plan.signals],
            len(_restructure_plan.ops),
            _restructure_plan.confidence,
        )
        _replay_print(
            f"  [{amendment_id}] StructuralTransformPlan: {[s.value for s in _restructure_plan.signals]}"
            f" | {len(_restructure_plan.ops)} ops | confidence={_restructure_plan.confidence:.2f}"
        )
        if restructure_plans_out is not None:
            if not any(
                _existing.amendment_id == amendment_id and _existing.ops == _restructure_plan.ops
                for _existing in restructure_plans_out
            ):
                restructure_plans_out.append(_restructure_plan)
        # Surface a degradation observation/finding when a chapter-level INSERT
        # plan still has a high proportion of uncovered body units.
        _emit_high_uncovered_degradation(
            HighUncoveredDegradationRequest(
                restructure_plan=_restructure_plan,
                amendment_id=amendment_id,
                uncovered_count=_restructure_uncov_count,
                total_units=_total_units,
                uncov_ratio=_uncov_ratio,
            ),
            HighUncoveredDegradationSinks(
                observations_out=observations_out,
                findings_out=findings_out,
            ),
        )
    # --- end restructure signal detection + plan ---
    # --- end typed coverage analysis ---

    has_content_ops = _compute_has_content_ops(ops, muutos_tree)

    # Pre-extract section labels explicitly mentioned in the preamble so
    # uncovered-body fallback can stay scoped to the cited statute surface.
    johto_mentioned_labels: Set[str] = set()
    johto_mentioned_new_chapters: Set[str] = set()
    johto_mentioned_replaced_chapters: Set[str] = set()
    moved_section_destinations: dict[str, str] = {}
    relabel_destination_sections: set[UncoveredSectionKey] = set()
    owned_chapter_labels: Set[str] = set(new_chapter_labels or ())
    for op in ops:
        if (
            op.op_type != "RENUMBER"
            or op.target_unit_kind != "section"
            or op.target_paragraph is not None
            or op.target_item
            or op.target_special
            or op.lo is None
            or op.lo.destination is None
            or not op.lo.destination.path
        ):
            continue
        dest_map = {
            kind: _norm_num_token(label)
            for kind, label in op.lo.destination.path
            if label
        }
        dest_section = dest_map.get("section")
        dest_chapter = dest_map.get("chapter") or _norm_num_token(op.target_chapter or "")
        dest_part = dest_map.get("part") or _norm_num_token(op.target_part or "")
        if dest_part:
            dest_part_arabic = _roman_label_to_arabic(dest_part)
            if dest_part_arabic is not None:
                dest_part = str(dest_part_arabic)
        if not dest_section or not dest_chapter:
            continue
        relabel_destination_sections.add(
            _uncovered_section_key(part=dest_part, chapter=dest_chapter, section=dest_section)
        )
    recovery_guards = UncoveredRecoveryGuards(
        covered_sections=covered_labels,
        chapter_payload_owned_sections=chapter_payload_owned_sections,
        relabel_destination_sections=relabel_destination_sections,
    )
    rstate = RecoveryState(
        amendment_id=amendment_id,
        op_source=op_source,
        findings_out=findings_out,
        guards=recovery_guards,
    )
    johto_el = muutos_tree.find(".//{*}preamble")
    if johto_el is not None:
        johto_text = etree.tostring(johto_el, method="text", encoding="unicode")
        # Single-item and range section references (e.g. "18 a §", "17―21 §").
        # The label group uses \d+\s*[a-z]? to capture space-separated
        # letter suffixes (e.g. "18 a") as well as adjacent ones ("18a").
        # The character class [-\u2014\u2013\u2015] covers hyphen, em-dash,
        # en-dash, and horizontal bar (U+2015, used in Finlex XML ranges).
        johto_mentioned_labels.update(_collect_johto_mentioned_section_labels(johto_text))
        chapter_mentions = collect_johto_chapter_scope_mentions(johto_text)
        johto_mentioned_new_chapters.update(chapter_mentions.new_chapter_labels)
        owned_chapter_labels.update(chapter_mentions.moved_destination_chapter_labels)
        johto_mentioned_replaced_chapters.update(chapter_mentions.replaced_chapter_labels)
        moved_section_destinations.update(
            {
                moved.section_label: moved.destination_chapter_label
                for moved in chapter_mentions.moved_section_destinations
            }
        )
    owned_chapter_labels.update(johto_mentioned_new_chapters)

    recovery_run = _UncoveredRecoveryRun(
        state=state,
        ops=ops,
        amendment_id=amendment_id,
        future_repeals=future_repeals,
        new_chapter_labels=new_chapter_labels,
        has_content_ops=has_content_ops,
        rstate=rstate,
        recovery_guards=recovery_guards,
        bp_assignments=_bp_assignments,
        johto_mentioned_labels=johto_mentioned_labels,
        johto_mentioned_replaced_chapters=johto_mentioned_replaced_chapters,
        moved_section_destinations=moved_section_destinations,
        owned_chapter_labels=owned_chapter_labels,
    )

    # --- Primary path: coverage analysis drives the loop ---
    # Iterate over supplemental_candidates from the typed coverage report.
    # Each gap's unit.payload_ref is the lxml <section> element, observed_label
    # is the normalized label, and parent_label is the chapter label (or None).
    # nonoperative/provenance sections have already been filtered to
    # ignore_nonoperative by analyze_coverage, so they won't appear here.
    #
    # Skip non-section units (chapter, article): chapter pre-creation is handled
    # by _pre_create_amendment_chapters (which runs before this function).
    # Passing a chapter element to _process_section_candidate would treat it as
    # a section with a chapter label (e.g. "2a"), producing wrong INSERT § 2a ops
    # that corrupt the tree and prevent child sections from being inserted.
    # Also skip sections that are already targeted by fine-grained PEG ops
    # (subsection/item level). A whole-section recovery would clobber the
    # deterministic subsection/item ops that PEG compiled.
    _peg_targeted_sections: Set[Tuple[Optional[str], str]] = set()
    _peg_targeted_labels: Set[str] = set()
    for _op in ops:
        if _op.target_unit_kind == "section" and _op.target_section:
            _norm_label = _norm_num_token(_op.target_section)
            _peg_targeted_sections.add((_op.target_chapter, _norm_label))
            _peg_targeted_labels.add(_norm_label)
    for _gap in _cov_report.supplemental_candidates:
        if _gap.unit.kind != "section":
            continue
        _sec_el = _gap.unit.payload_ref
        if _sec_el is None:
            continue
        _gap_label = _gap.unit.observed_label or ""
        if not _gap_label:
            continue
        _gap_chapter = _gap.unit.parent_label  # May be None for top-level sections
        # Skip sections already targeted by PEG-compiled ops in the same chapter.
        if (_gap.unit.parent_label, _gap_label) in _peg_targeted_sections:
            recovery_run.record_skip("peg_owned_same_chapter", _gap_label, _gap_chapter)
            continue
        # Also skip when PEG already owns the same section label in a different
        # chapter. In that case the body chapter is stale/misleading, and
        # uncovered-body recovery must not manufacture a duplicate same-labeled
        # section under the body's chapter.
        if _gap_label in _peg_targeted_labels:
            recovery_run.record_skip("peg_owned_label_collision", _gap_label, _gap_chapter)
            continue
        recovery_run.process_section_candidate(
            cast(etree._Element, _sec_el),
            _gap_label,
            _gap_chapter,
        )

    # The typed coverage sweep above is the sole candidate enumeration. It
    # formerly ran alongside a legacy ad-hoc raw-body section scan (the
    # "dual-run", LAWVM_DUAL_UNCOVERED) that re-walked every body <section>.
    # A full A/B across the bench corpus showed the raw scan was strictly
    # score-neutral — zero per-statute differences — while emitting ~23k
    # redundant peg-owned skip findings that polluted the audit trail. Its two
    # unique filters (malformed "X luku" chapter markers and the amending act's
    # own "Tällä lailla kumotaan" self-repeal provision) are already excluded
    # from supplemental_candidates by extract_body_coverage's nonoperative
    # tagging, so removing the raw scan changes no recovered op and only drops
    # the spurious findings.

    rstate.emit_chapter_payload_mixed_findings()

    return UncoveredBodyRecoveryResult(
        recovered_ops=tuple(rstate.result),
        candidate_audits=tuple(rstate.audits),
    )


def _same_amendment_non_repeal_section_labels(
    *,
    lo_ops_out: Optional[List[_LegalOperation]],
    amendment_id: str,
) -> Set[str]:
    labels: Set[str] = set()
    if lo_ops_out is None:
        return labels
    for lo in lo_ops_out:
        if lo.source is None or lo.source.statute_id != amendment_id:
            continue
        if lo.action is StructuralAction.REPEAL:
            continue
        if not lo.target.path or lo.target.path[-1][0] != "section":
            continue
        labels.add(_norm_num_token(lo.target.path[-1][1]))
    return labels


def _prior_same_effective_container_replacement(
    *,
    lo_ops_out: Optional[List[_LegalOperation]],
    op_source: Optional[OperationSource],
    amendment_id: str,
    target_path: tuple[tuple[str, str], ...],
) -> _LegalOperation | None:
    if lo_ops_out is None or op_source is None or not op_source.effective:
        return None
    for prior in reversed(lo_ops_out):
        if prior.action not in (StructuralAction.INSERT, StructuralAction.REPLACE):
            continue
        if prior.target is None or tuple(prior.target.path) != target_path:
            continue
        prior_source = prior.source
        if prior_source is None:
            continue
        if prior_source.effective != op_source.effective:
            continue
        if prior_source.statute_id == amendment_id:
            continue
        return prior
    return None


def _apply_uncovered_kumotaan_typed(
    request: KumotaanRecoveryRequest,
    sinks: Optional[KumotaanRecoverySinks] = None,
) -> KumotaanRecoveryResult:
    """Apply uncovered repeals from kumotaan clauses."""
    sinks = sinks or KumotaanRecoverySinks()
    state = request.state
    ctx = request.ctx
    ops = request.ops
    johto = request.johto
    amendment_id = request.amendment_id
    lo_ops_out = sinks.lo_ops_out
    op_source = request.op_source
    findings_out = sinks.findings_out
    source_pathologies_out = sinks.source_pathologies_out

    vts_section_refs = [
        _norm_num_token(op.target_section)
        for op in ops
        if (
            op.voimaantulo_repeal
            and op.target_unit_kind == "section"
            and op.target_section
            and not op.target_paragraph
            and not op.target_item
            and not op.target_special
        )
    ]
    vts_granular_section_refs = {
        _norm_num_token(op.target_section)
        for op in ops
        if (
            op.voimaantulo_repeal
            and op.target_unit_kind == "section"
            and op.target_section
            and (op.target_paragraph or op.target_item or op.target_special)
        )
    }
    vts_container_refs: dict[TargetUnitKind, list[str]] = {"chapter": [], "part": []}
    for op in ops:
        if not op.voimaantulo_repeal or not op.target_section:
            continue
        if op.target_unit_kind in {"chapter", "part"}:
            vts_container_refs[op.target_unit_kind].append(_norm_num_token(op.target_section))

    if not johto or "kumotaan" not in johto.lower():
        if not vts_section_refs and not vts_container_refs["chapter"] and not vts_container_refs["part"]:
            return KumotaanRecoveryResult(state=state)

    has_peg_repeals = any(op.op_type == "REPEAL" for op in ops)
    has_vts_repeals = bool(vts_section_refs or vts_container_refs["chapter"] or vts_container_refs["part"])
    if not has_peg_repeals and not has_vts_repeals and not re.search(r"\bkumotaan\b", johto, re.IGNORECASE):
        return KumotaanRecoveryResult(state=state)

    covered_labels: Set[str] = set()
    covered_containers: Set[CoveredContainerKey] = set()
    for op in ops:
        if op.voimaantulo_repeal:
            continue
        if op.target_unit_kind == "section" and op.target_section:
            # A heading-only op ("X §:n otsikko" / "X §:n edellä oleva
            # väliotsikko") targets the section heading, not the section body.
            # When a "kumotaan X § ja sen edellä oleva väliotsikko" clause repeals
            # the section together with its preceding subheading, the heading op
            # must not mask the section repeal: the section body still has to be
            # tombstoned by the kumotaan recovery below.
            if op.target_special in {"otsikko", "otsikko_edella"}:
                continue
            covered_labels.add(_norm_num_token(op.target_section))
        elif op.target_unit_kind in {"chapter", "part"} and op.target_section:
            covered_containers.add(
                CoveredContainerKey(
                    target_unit_kind=op.target_unit_kind,
                    label=_norm_num_token(op.target_section),
                )
            )
    covered_labels |= _same_amendment_non_repeal_section_labels(
        lo_ops_out=lo_ops_out,
        amendment_id=amendment_id,
    )

    # Chapter-scoped kumotaan section refs.  Extracting only bare section labels
    # discards the "N luvun" chapter context, so a clause like "10 luvun 5 d §"
    # collapses to bare "5d".  When the same section number lives in several
    # chapters (e.g. 5 d § exists in both chapter 1 and chapter 10), the
    # recovery's unscoped find_section_path(label) then resolved the FIRST
    # document-order match and repealed the wrong section, leaving the
    # genuinely-repealed one live.  Carry the chapter so the lookup targets the
    # address the johtolause actually named.
    kumotaan_chap_map = _extract_kumotaan_chapter_section_map(johto)
    kumotaan_section_targets: List[tuple[Optional[str], str]] = []
    seen_section_targets: Set[tuple[Optional[str], str]] = set()
    for chapter_label, labels in kumotaan_chap_map.items():
        for label in labels:
            target = (chapter_label, label)
            if label and target not in seen_section_targets:
                kumotaan_section_targets.append(target)
                seen_section_targets.add(target)
    for label in vts_section_refs:
        target = (None, label)
        if label and target not in seen_section_targets:
            kumotaan_section_targets.append(target)
            seen_section_targets.add(target)
    kumotaan_containers = _extract_kumotaan_container_refs(johto)
    for kind_name, labels in vts_container_refs.items():
        if labels:
            kumotaan_containers.setdefault(kind_name, [])
            for label in labels:
                if label and label not in kumotaan_containers[kind_name]:
                    kumotaan_containers[kind_name].append(label)

    repealed: List[str] = []
    finding_emitter = KumotaanRecoveryFindingEmitter(
        amendment_id=amendment_id,
        findings_out=findings_out,
    )

    for chapter_label, ref in kumotaan_section_targets:
        label = _norm_num_token(ref)
        if not label:
            finding_emitter.append_skip(
                target_norm=str(ref),
                reason="kumotaan_empty_section_ref",
            )
            continue
        if label in covered_labels:
            finding_emitter.append_skip(
                target_norm=label,
                reason="kumotaan_section_already_covered",
            )
            continue
        if label in vts_granular_section_refs and label not in vts_section_refs:
            finding_emitter.append_skip(
                target_norm=label,
                reason="kumotaan_granular_vts_repeal",
            )
            continue
        covered_labels.add(label)

        sec_path = state.find_section_path(label, chapter_label)
        if sec_path is None:
            finding_emitter.append_skip(
                target_norm=label,
                reason="kumotaan_missing_section_target",
            )
            continue

        sec_node = _tops.resolve(state.ir, sec_path)
        assert sec_node is not None, f"resolve failed for {sec_path}"
        _base_path = _tops.find(
            ctx.base_ir,
            "section",
            label,
            scope_kind=IRNodeKind.CHAPTER.value if chapter_label else None,
            scope_label=chapter_label,
        )
        base_sec = _tops.resolve(ctx.base_ir, _base_path) if _base_path is not None else None
        if base_sec is not None:
            # Extract issue date from op_source if available
            _issue = None
            if op_source and op_source.enacted:
                try:
                    _issue = dt.date.fromisoformat(op_source.enacted)
                except ValueError:
                    pass
            _title = op_source.title if op_source else ""
            ph = _build_repeal_placeholder_ir(sec_node, label, amendment_id, _issue, _title)
            state = state.with_ir(
                _tops.replace_at(state.ir, sec_path, ph),
                preserve_provision_index=True,
            )
            repealed.append(label)
            op_payload = ph
            op_action = StructuralAction.REPLACE
        else:
            state = state.with_ir(_tops.remove_at(state.ir, sec_path))
            repealed.append(f"{label} (drop)")
            op_payload = None
            op_action = StructuralAction.REPEAL

        op_id = f"uncovered_repeal_{label}"
        if lo_ops_out is not None:
            # Use resolved path (strip empty-label elements like hcontainer)
            tl_path = tuple((k, v) for k, v in sec_path if v)
            lo_ops_out.append(
                _LegalOperation(
                    op_id=op_id,
                    sequence=0,
                    action=op_action,
                    target=LegalAddress(path=tl_path),
                    payload=op_payload,
                    source=op_source,
                    group_id=f"finland-johto:{amendment_id}",
                    witness_rule_id=FI_RECOVERY_UNCOVERED_KUMOTAAN_RULE_ID,
                )
            )
        finding_emitter.append(
            op_id=op_id,
            target_unit_kind="section",
            target_norm=label,
        )

    repealed_containers: List[str] = []

    for target_unit_kind, refs in kumotaan_containers.items():
        kind_name = "luku" if target_unit_kind == "chapter" else "osa"
        node_kind = "chapter" if target_unit_kind == "chapter" else "part"
        for ref in refs:
            label = _norm_num_token(ref)
            if not label:
                finding_emitter.append_skip(
                    target_norm=str(ref),
                    reason=f"kumotaan_empty_{target_unit_kind}_ref",
                )
                continue
            existing_path = state.find(node_kind, label)
            covered_key = CoveredContainerKey(target_unit_kind=target_unit_kind, label=label)
            if covered_key in covered_containers and existing_path is None:
                finding_emitter.append_skip(
                    target_norm=label,
                    reason=f"kumotaan_{target_unit_kind}_covered_absent",
                )
                continue
            covered_containers.add(covered_key)

            if existing_path is None:
                finding_emitter.append_skip(
                    target_norm=label,
                    reason=f"kumotaan_missing_{target_unit_kind}_target",
                )
                continue

            tl_path = tuple((k, v) for k, v in existing_path if v)
            shadow = _prior_same_effective_container_replacement(
                lo_ops_out=lo_ops_out,
                op_source=op_source,
                amendment_id=amendment_id,
                target_path=tl_path,
            )
            if shadow is not None:
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_same_effective_container_repeal_shadowed_pathology(
                            source_statute=amendment_id,
                            target_unit_kind=target_unit_kind,
                            target_label=f"{label} {kind_name}",
                            prior_source_statute=shadow.source.statute_id if shadow.source else "",
                            effective=op_source.effective if op_source is not None else "",
                        )
                    )
                continue

            state = state.with_ir(_tops.remove_at(state.ir, existing_path))
            repealed_containers.append(f"{label} {kind_name}")

            op_id = f"uncovered_repeal_{target_unit_kind}_{label}"
            if lo_ops_out is not None:
                lo_ops_out.append(
                        _LegalOperation(
                            op_id=op_id,
                            sequence=0,
                            action=StructuralAction.REPEAL,
                        target=LegalAddress(path=tl_path),
                        payload=None,
                        source=op_source,
                        group_id=f"finland-johto:{amendment_id}",
                        witness_rule_id=FI_RECOVERY_UNCOVERED_KUMOTAAN_RULE_ID,
                    )
                )
            finding_emitter.append(
                op_id=op_id,
                target_unit_kind=target_unit_kind,
                target_norm=label,
            )

    if repealed:
        _replay_print(f"  [{amendment_id}] uncovered kumotaan: {repealed}")
    if repealed_containers:
        _replay_print(f"  [{amendment_id}] uncovered kumotaan containers: {repealed_containers}")
    return KumotaanRecoveryResult(state=state)


def _prescan_source_excerpt(xml_bytes: bytes | None) -> str:
    if not xml_bytes:
        return ""
    return re.sub(r"\s+", " ", xml_bytes.decode("utf-8", errors="replace")).strip()[:160]


def _record_prescan_diagnostic(
    diagnostics_out: Optional[List[PreScanRepealDiagnostic]],
    *,
    reason_code: PreScanRepealDiagnosticReason,
    source_reason: str,
    source_statute: str,
    xml_bytes: bytes | None = None,
    exc: BaseException | None = None,
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        PreScanRepealDiagnostic(
            rule_id=PRESCAN_REPEAL_TARGET_DIAGNOSTIC_RULE_ID,
            reason_code=reason_code,
            source_reason=source_reason,
            source_statute=source_statute,
            source_excerpt=_prescan_source_excerpt(xml_bytes),
            exception_type=exc.__class__.__name__ if exc is not None else "",
            exception_message=str(exc)[:240] if exc is not None else "",
        )
    )


def _pre_scan_repeal_targets(
    request: PreScanRepealTargetsRequest,
    sinks: Optional[PreScanRepealTargetsSinks] = None,
) -> "List[Set[RepealTargetRef]]":
    """Scan amendment schedule and return per-amendment REPEAL target sets.

    For amendment at index ``i`` the returned set contains typed repeal-target
    refs for every REPEAL op extracted from amendments ``i`` onwards.

    Callers typically compute the *future* repeals for amendment ``i`` as the
    union of sets ``i+1 .. n``.  Storing per-amendment gives callers the
    flexibility to also inspect the current amendment's own repeals.

    Extraction is intentionally lightweight — only the johtolause PEG parser
    and voimaantulo-repeal extractor are used (no repair chain, no body
    traversal).  False positives are acceptable: they suppress an uncovered
    body insert that would have been removed later anyway.  False negatives
    (missed repeals) are also acceptable: they result in the pre-existing
    over-insertion behaviour.
    """
    muutoslait = request.muutoslait
    corpus_store = request.corpus_store
    parent_id = request.parent_id
    parent_title = request.parent_title
    cutoff_date = request.cutoff_date
    vts_skipped_targets_out = (
        sinks.vts_skipped_targets_out if sinks is not None else None
    )
    vts_source_diagnostics_out = (
        sinks.vts_source_diagnostics_out if sinks is not None else None
    )
    prescan_diagnostics_out = (
        sinks.prescan_diagnostics_out if sinks is not None else None
    )

    per_amendment: List[Set[RepealTargetRef]] = []

    for amendment_id in muutoslait:
        targets: Set[RepealTargetRef] = set()
        xml_bytes = corpus_store.read_source(amendment_id)
        if xml_bytes is None:
            _record_prescan_diagnostic(
                prescan_diagnostics_out,
                reason_code="missing_source",
                source_reason="future-repeal pre-scan could not read amendment source",
                source_statute=amendment_id,
            )
            per_amendment.append(targets)
            continue
        try:
            tree = etree.fromstring(xml_bytes)
            eff_date = _amendment_effective_date(tree)
            if cutoff_date is not None and eff_date is not None and eff_date > cutoff_date:
                per_amendment.append(targets)
                continue
            acquisition = build_amendment_acquisition_result(
                xml_bytes=xml_bytes,
                parent_id=parent_id,
                amendment_id=amendment_id,
                source_title="",
                parent_title=parent_title,
            )
            # Pre-scan now follows the same typed acquisition decision as the
            # main ingress. Keep the normalized string shape here because this
            # helper is intentionally lightweight and PEG-facing.
            johto = acquisition.decision.chosen_normalized_text
            # Only scan amendments that have repeal keywords.
            if johto and "kumotaan" in johto.lower():
                legal_ops = extract_johtolause_legal_ops(johto)
                for lo in legal_ops:
                    if lo.action is not StructuralAction.REPEAL:
                        continue
                    # Unpack target path via the same logic as _lo_target_fields.
                    # Only record WHOLE-SECTION or WHOLE-CHAPTER repeals —
                    # a repeal of "section 57 subsection 2" is a partial repeal
                    # and must NOT suppress insertion of section 57 itself.
                    pd = {k: v for k, v in lo.target.path}
                    has_sub = "subsection" in pd or "paragraph" in pd or "item" in pd
                    if "section" in pd and not has_sub:
                        sec_norm = _norm_num_token(str(pd["section"]))
                        ch_raw = pd.get("chapter")
                        ch_norm: Optional[str] = _norm_num_token(str(ch_raw)).removesuffix("luku") if ch_raw else None
                        targets.add(RepealTargetRef.section(sec_norm, ch_norm))
                    elif "chapter" in pd and not has_sub:
                        ch_norm = _norm_num_token(str(pd["chapter"])).removesuffix("luku")
                        targets.add(RepealTargetRef.chapter(ch_norm))
            # Also pick up voimaantulo-style repeals (e.g. whole-statute replacements).
            if parent_id:
                try:
                    vts_ops = extract_voimaantulo_repeals(
                        xml_bytes,
                        parent_id,
                        parent_title=parent_title,
                        skipped_targets_out=vts_skipped_targets_out,
                        source_diagnostics_out=vts_source_diagnostics_out,
                    )
                    for op in vts_ops:
                        sec_n = _norm_num_token(op.target_section) if op.target_section else ""
                        ch_n: Optional[str] = (
                            _norm_num_token(op.target_chapter).removesuffix("luku") if op.target_chapter else None
                        )
                        if sec_n:
                            targets.add(RepealTargetRef(op.target_unit_kind, sec_n, ch_n))
                except (ValueError, KeyError, AttributeError, TypeError, IndexError) as exc:
                    _record_prescan_diagnostic(
                        prescan_diagnostics_out,
                        reason_code="vts_extraction_error",
                        source_reason="future-repeal pre-scan VTS extraction failed",
                        source_statute=amendment_id,
                        xml_bytes=xml_bytes,
                        exc=exc,
                    )
        except (ValueError, KeyError, AttributeError, TypeError, IndexError, etree.XMLSyntaxError) as exc:
            _record_prescan_diagnostic(
                prescan_diagnostics_out,
                reason_code="prescan_parse_error",
                source_reason="future-repeal pre-scan could not inspect amendment source",
                source_statute=amendment_id,
                xml_bytes=xml_bytes,
                exc=exc,
            )
        per_amendment.append(targets)

    return per_amendment

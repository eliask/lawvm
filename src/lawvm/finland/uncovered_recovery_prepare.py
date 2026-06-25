"""Preparation phase for Finnish uncovered-body recovery."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Dict, List, Optional, cast

from lawvm.core.coverage import CoverageIgnoredUnit, CoverageRejectedClaim, CoverageReport
from lawvm.core.phase_result import Finding
from lawvm.finland.body_coverage import (
    analyze_coverage,
    collect_coverage_claims,
)
from lawvm.finland.body_pairing import (
    PayloadAssignment,
    assign_body_units_subtree_aware,
    build_chapter_subtree_coverage,
    build_clause_claims as _bp_build_clause_claims,
    clause_ast_from_amendment_ops as _bp_clause_ast_from_ops,
    enforce_pairing_invariants,
)
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import AmendmentOp, FailedOp
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.finland.replay_notices import replay_verbose_enabled
from lawvm.finland.restructure_plan import (
    RestructureSignal,
    StructuralTransformPlan,
    build_restructure_plan,
)
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.uncovered_recovery_context import (
    UncoveredRecoveryContext,
)
from lawvm.finland.uncovered_recovery_findings import (
    CoverageIgnoredUnitFindingRequest,
    CoverageUnresolvedGapFindingRequest,
    HighUncoveredBodyDegradedFindingRequest,
    _coverage_ignored_unit_finding,
    _coverage_rejected_claim_finding,
    _coverage_unresolved_gap_finding,
    _high_uncovered_body_degraded_finding,
)
from lawvm.finland.uncovered_recovery_state import (
    UncoveredRecoveryGuards,
    UncoveredSectionKey,
    uncovered_section_key,
)

logger = logging.getLogger(__name__)

AnalyzeCoverageFn = Callable[..., CoverageReport]
AssignBodyUnitsFn = Callable[..., object]


@dataclass(frozen=True, slots=True)
class UncoveredRecoveryPreparationRequest:
    """Inputs for the read-only uncovered-body preparation phase."""

    statute_id: str
    amendment_id: str
    ops: List[AmendmentOp]
    source_model: AmendmentSourceModel
    failed_ops_out: Optional[List[FailedOp]]
    new_chapter_labels: Optional[set[str]]
    restructure_plans_out: Optional[List[StructuralTransformPlan]]
    observations_out: Optional[List[Dict[str, object]]]
    findings_out: Optional[List[Finding]]
    analyze_coverage_fn: AnalyzeCoverageFn = analyze_coverage
    assign_body_units_fn: AssignBodyUnitsFn = assign_body_units_subtree_aware


@dataclass(frozen=True, slots=True)
class UncoveredRecoveryPreparation:
    """Prepared evidence/control inputs for sequential candidate recovery."""

    cov_report: CoverageReport
    recovery_guards: UncoveredRecoveryGuards
    body_pairing_assignments: object
    has_content_ops: bool
    context: UncoveredRecoveryContext
    has_body: bool
    restructure_plan: Optional[StructuralTransformPlan]


def prepare_uncovered_body_recovery(
    request: UncoveredRecoveryPreparationRequest,
) -> UncoveredRecoveryPreparation:
    """Run coverage, body-pairing, restructure, and scope preparation."""
    ops = request.ops
    source_model = request.source_model
    statute_id = request.statute_id
    amendment_id = request.amendment_id
    findings_out = request.findings_out

    covered_labels = _build_peg_covered_sets(ops, request.failed_ops_out, source_model)

    ignored_units: list[CoverageIgnoredUnit] = []
    rejected_claims: list[CoverageRejectedClaim] = []
    cov_units = list(source_model.body_coverage_units(ignored_units_out=ignored_units))
    cov_claims = collect_coverage_claims(ops, rejected_claims_out=rejected_claims)
    cov_report = request.analyze_coverage_fn(
        cov_units,
        cov_claims,
        ignored_units=ignored_units,
        rejected_claims=rejected_claims,
    )
    _emit_coverage_analysis_findings(cov_report, findings_out, amendment_id)

    body_pairing_assignments: object = ()
    chapter_payload_owned_sections: set[UncoveredSectionKey] = set()
    restructure_plan: Optional[StructuralTransformPlan] = None
    has_body = source_model.has_body
    if has_body:
        restructure_uncov_count = _restructure_uncovered_count(cov_report)
        total_units = len(cov_units)
        uncov_ratio = restructure_uncov_count / total_units if total_units > 0 else 0.0
        if cov_report.uncovered_count > 0:
            operative_unit_count = sum(
                1
                for unit in cov_units
                if not ("container" in unit.tags and unit.kind == "chapter")
            )
            _replay_print(
                f"  [{amendment_id}] Coverage: {operative_unit_count} units, "
                f"{len(cov_claims)} claimed, "
                f"{cov_report.uncovered_count} uncovered"
            )

        body_pairing = _prepare_body_pairing(
            statute_id=statute_id,
            amendment_id=amendment_id,
            ops=ops,
            source_model=source_model,
            assign_body_units_fn=request.assign_body_units_fn,
        )
        body_pairing_assignments = body_pairing.assignments
        chapter_payload_owned_sections = body_pairing.chapter_payload_owned_sections
        restructure_plan = _prepare_restructure_plan(
            statute_id=statute_id,
            amendment_id=amendment_id,
            ops=ops,
            uncov_ratio=uncov_ratio,
            total_units=total_units,
            body_unit_ids_by_chapter=body_pairing.body_unit_ids_by_chapter,
            restructure_uncov_count=restructure_uncov_count,
            restructure_plans_out=request.restructure_plans_out,
            observations_out=request.observations_out,
            findings_out=findings_out,
        )

    context = source_model.build_uncovered_recovery_context(
        ops=ops,
        new_chapter_labels=request.new_chapter_labels,
    )
    recovery_guards = UncoveredRecoveryGuards(
        covered_sections=covered_labels,
        chapter_payload_owned_sections=chapter_payload_owned_sections,
        relabel_destination_sections=set(context.relabel_destination_sections),
    )
    return UncoveredRecoveryPreparation(
        cov_report=cov_report,
        recovery_guards=recovery_guards,
        body_pairing_assignments=body_pairing_assignments,
        has_content_ops=source_model.has_uncovered_recovery_content_ops(ops),
        context=context,
        has_body=has_body,
        restructure_plan=restructure_plan,
    )


def _build_peg_covered_sets(
    ops: List[AmendmentOp],
    failed_ops_out: Optional[List[FailedOp]],
    source_model: AmendmentSourceModel,
) -> set[UncoveredSectionKey]:
    """Section labels already covered by PEG ops."""
    failed_sections: set[str] = set()
    if failed_ops_out:
        for fop in failed_ops_out:
            if fop.target_unit_kind == "section" and fop.target_section:
                failed_sections.add(_norm_num_token(fop.target_section))
    covered_labels: set[UncoveredSectionKey] = set()
    for op in ops:
        if op.target_cols.target_unit_kind == "section" and op.target_cols.target_section:
            label = _norm_num_token(op.target_cols.target_section)
            if label not in failed_sections:
                covered_labels.add(
                    uncovered_section_key(
                        part=op.target_cols.target_part,
                        chapter=op.target_cols.target_chapter,
                        section=label,
                    )
                )
                source_body_scope = source_model.source_body_scope_for_section_target(label)
                if source_body_scope is not None:
                    source_part, source_chapter = source_body_scope
                    covered_labels.add(
                        uncovered_section_key(
                            part=source_part,
                            chapter=source_chapter,
                            section=label,
                        )
                    )
    return covered_labels


def _emit_coverage_analysis_findings(
    cov_report: CoverageReport,
    findings_out: Optional[List[Finding]],
    amendment_id: str,
) -> None:
    """Emit ignored-unit, rejected-claim, and unresolved-gap findings."""
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
class _BodyPairingPreparation:
    assignments: object
    chapter_payload_owned_sections: set[UncoveredSectionKey]
    body_unit_ids_by_chapter: dict[tuple[str, str], list[str]]


def _prepare_body_pairing(
    *,
    statute_id: str,
    amendment_id: str,
    ops: List[AmendmentOp],
    source_model: AmendmentSourceModel,
    assign_body_units_fn: AssignBodyUnitsFn,
) -> _BodyPairingPreparation:
    inventory = list(source_model.observed_body_inventory())
    ast = _bp_clause_ast_from_ops(ops)
    claims = _bp_build_clause_claims(ast, statute_id)
    assignments = cast(List[PayloadAssignment], assign_body_units_fn(inventory, claims, statute_id))
    findings = enforce_pairing_invariants(assignments, statute_id, amendment_id)
    if findings:
        for finding in findings:
            logger.debug("  [%s] body-pairing: %s: %s", amendment_id, finding.kind, finding.detail)
    inventory_by_id = {unit.unit_id: unit for unit in inventory}
    chapter_payload_owned_sections: set[UncoveredSectionKey] = set()
    for assignment in assignments:
        if assignment.status != "claimed_current" or assignment.claim is None:
            continue
        unit = inventory_by_id.get(assignment.body_unit_id)
        if unit is None or unit.kind != "section" or not unit.chapter_label:
            continue
        claim = assignment.claim
        if (
            claim.target_statute == statute_id
            and claim.claim_kind == "INSERT"
            and claim.chapter == ""
            and claim.target_address == unit.chapter_label
        ):
            chapter_payload_owned_sections.add(
                uncovered_section_key(
                    part=unit.part_label,
                    chapter=unit.chapter_label,
                    section=unit.label,
                )
            )

    chapter_subtree_coverage = build_chapter_subtree_coverage(inventory, claims, statute_id)
    body_unit_ids_by_chapter: dict[tuple[str, str], list[str]] = dict(chapter_subtree_coverage)
    for unit in inventory:
        if unit.kind == "section" and unit.chapter_label:
            chapter_key = (unit.part_label, unit.chapter_label)
            if chapter_key not in body_unit_ids_by_chapter:
                body_unit_ids_by_chapter.setdefault(chapter_key, []).append(unit.unit_id)
    return _BodyPairingPreparation(
        assignments=assignments,
        chapter_payload_owned_sections=chapter_payload_owned_sections,
        body_unit_ids_by_chapter=body_unit_ids_by_chapter,
    )


def _restructure_uncovered_count(cov_report: CoverageReport) -> int:
    container_chapter_gaps = sum(
        1
        for gap in cov_report.gaps
        if "container" in gap.unit.tags
        and gap.unit.kind == "chapter"
        and gap.disposition == "covered_by_broad_scope"
    )
    return cov_report.uncovered_count + container_chapter_gaps


def _prepare_restructure_plan(
    *,
    statute_id: str,
    amendment_id: str,
    ops: List[AmendmentOp],
    uncov_ratio: float,
    total_units: int,
    body_unit_ids_by_chapter: dict[tuple[str, str], list[str]],
    restructure_uncov_count: int,
    restructure_plans_out: Optional[List[StructuralTransformPlan]],
    observations_out: Optional[List[Dict[str, object]]],
    findings_out: Optional[List[Finding]],
) -> Optional[StructuralTransformPlan]:
    restructure_plan: Optional[StructuralTransformPlan] = build_restructure_plan(
        statute_id,
        amendment_id,
        ops=list(ops),
        uncov_ratio=uncov_ratio,
        total_units=total_units,
        body_unit_ids_by_chapter=body_unit_ids_by_chapter,
    )
    if restructure_plan is None:
        return None
    logger.info(
        "  [%s] StructuralTransformPlan built: signals=%s, ops=%d, confidence=%.2f",
        amendment_id,
        [signal.value for signal in restructure_plan.signals],
        len(restructure_plan.ops),
        restructure_plan.confidence,
    )
    _replay_print(
        f"  [{amendment_id}] StructuralTransformPlan: {[s.value for s in restructure_plan.signals]}"
        f" | {len(restructure_plan.ops)} ops | confidence={restructure_plan.confidence:.2f}"
    )
    if restructure_plans_out is not None and not any(
        existing.amendment_id == amendment_id and existing.ops == restructure_plan.ops
        for existing in restructure_plans_out
    ):
        restructure_plans_out.append(restructure_plan)
    _emit_high_uncovered_degradation(
        HighUncoveredDegradationRequest(
            restructure_plan=restructure_plan,
            amendment_id=amendment_id,
            uncovered_count=restructure_uncov_count,
            total_units=total_units,
            uncov_ratio=uncov_ratio,
        ),
        HighUncoveredDegradationSinks(
            observations_out=observations_out,
            findings_out=findings_out,
        ),
    )
    return restructure_plan


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
    """Surface a degradation observation/finding for high-uncovered chapter INSERT plans."""
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
    signals = [signal.value for signal in restructure_plan.signals]
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
            amendment_id,
            uncovered_count,
            total_units,
            uncov_ratio,
            restructure_plan.confidence,
        )

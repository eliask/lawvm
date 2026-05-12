"""Uncovered body recovery subsystem, extracted from grafter.py.

This module handles the fallback path where the johtolause parser missed
structural ops in an amendment body, and synthesizes them by scanning the
raw body and applying repeal/insert heuristics.  It corresponds to the
``_recover_uncovered_body_ops`` family of functions that were previously
inlined in grafter.py.

Functions exported:
  _strict_rejected_uncovered_body_finding
  _uncovered_body_recovery_finding
  _recover_uncovered_body_ops
  _apply_uncovered_kumotaan
  _pre_scan_repeal_targets
"""

from __future__ import annotations

import logging
import re
import datetime as dt
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple, cast

import lxml.etree as etree

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    OperationSource,
)
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.phase_result import Finding
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core import tree_ops as _tops
from lawvm.core.coverage import CoverageIgnoredUnit, CoverageRejectedClaim

from lawvm.finland.ops import (
    OpType,
    AmendmentOp,
    ResolvedOp,
    FailedOp,
)
from lawvm.finland.helpers import (
    _norm_num_token,
    _roman_label_to_arabic,
    _is_omission_ir,
    _fi_label_postprocessor,
)
from lawvm.finland.body_coverage import (
    extract_body_coverage,
    collect_coverage_claims,
    analyze_coverage,
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
from lawvm.finland.merge import (
    _has_section_omissions_ir,
    _merge_section_with_omission_ir,
)
from lawvm.finland.apply_ir_ops import (
    _build_repeal_placeholder_ir,
    _relabel_section_ir,
)
from lawvm.finland.kumotaan import (
    _extract_kumotaan_section_refs,
    _extract_kumotaan_container_refs,
)
from lawvm.finland.metadata import (
    _amendment_effective_date,
)
from lawvm.finland.payload_normalize import PayloadCompletenessWitness
from lawvm.finland.acquisition import build_amendment_acquisition_result
from lawvm.finland.vts import VtsSkippedTarget, VtsSourceDiagnostic, extract_voimaantulo_repeals
from lawvm.finland.johtolause import extract_legal_ops as extract_johtolause_legal_ops
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.constraints import DEBUG
from lawvm.finland.replay_notices import replay_print as _replay_print
from lawvm.xml_ingest import _tag

if TYPE_CHECKING:
    from lawvm.finland.grafter import RepealTargetRef
    from lawvm.finland.statute import ReplayState, StatuteContext
    from lawvm.corpus_store import CorpusStore

logger = logging.getLogger(__name__)


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
    """Build the blocking finding for strict-profile uncovered-body rejection."""
    return Finding(
        kind="APPLY.STRICT_REJECTED_UNCOVERED_BODY",
        role="obligation",
        stage=stage,
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": (
                "Uncovered body recovery rejected by strict profile "
                "(allows_uncovered_body_recovery=False)"
            ),
        },
    )


def _uncovered_body_recovery_finding(
    *,
    op_id: str,
    source_statute: str,
    target_unit_kind: str,
    target_norm: str,
    target_chapter: str | None = None,
    target_part: str | None = None,
) -> Finding | None:
    """Build the replay-owned finding for one uncovered-body recovery action."""
    if op_id.startswith("uncovered_replace_"):
        kind = "APPLY.FALLBACK_WHOLE_SECTION_REPLACE"
        message = "Fallback whole-section replacement was used."
    elif op_id.startswith("uncovered_insert_"):
        kind = "APPLY.UNCOVERED_BODY_RECOVERY"
        message = "Uncovered-body insertion supplement was used."
    elif op_id.startswith("uncovered_merge_"):
        kind = "ELAB.OMISSION_EXPANSION"
        message = "Omission-expansion merge was used."
    elif op_id.startswith("uncovered_repeal_"):
        kind = "APPLY.UNCOVERED_BODY_RECOVERY"
        message = "Uncovered-body repeal recovery was used."
    else:
        return None

    detail: dict[str, object] = {
        "message": message,
        "op_id": op_id,
        "target_unit_kind": target_unit_kind,
        "target_norm": target_norm,
    }
    if target_chapter:
        detail["target_chapter"] = target_chapter
    if target_part:
        detail["target_part"] = target_part

    spec = get_finding_spec(kind)
    if spec is not None and spec.role == "obligation":
        return Finding(
            kind=kind,
            role="obligation",
            stage="apply",
            blocking=True,
            source_statute=source_statute,
            detail={
                **detail,
                "barrier_code": kind,
            },
        )

    return Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply",
        blocking=True,
        source_statute=source_statute,
        detail={
            **detail,
            "barrier_code": kind,
        },
    )


def _uncovered_body_recovery_skipped_finding(
    *,
    source_statute: str,
    target_section: str,
    reason: str,
    target_chapter: str | None = None,
    target_part: str | None = None,
) -> Finding:
    specific_kind = {
        "duplicate_recovered_candidate": "APPLY.UNCOVERED_BODY_DUPLICATE_CANDIDATE",
        "cross_chapter_existing_target": "APPLY.UNCOVERED_BODY_CROSS_CHAPTER_COLLISION",
        "moved_destination_mismatch": "APPLY.UNCOVERED_BODY_MOVED_DESTINATION_MISMATCH",
        "same_wave_relabel_destination_owned": "APPLY.UNCOVERED_BODY_RELABEL_DESTINATION_OWNED",
        "body_pairing_guard": "APPLY.UNCOVERED_BODY_BODY_PAIRING_GUARD",
        "no_content_ops": "APPLY.UNCOVERED_BODY_NO_CONTENT_OPS",
        "would_lose_subsections": "APPLY.UNCOVERED_BODY_WOULD_LOSE_SUBSECTIONS",
        "past_repeal_placeholder_guard": "APPLY.UNCOVERED_BODY_PAST_REPEAL_GUARD",
        "johto_guard": "APPLY.UNCOVERED_BODY_JOHTO_GUARD",
        "omission_merge_failed": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_FAILED",
        "omission_merge_low_text_ratio": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_LOW_TEXT_RATIO",
        "omission_merge_duplicate_subsection_labels": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_DUPLICATE_LABELS",
        "omission_merge_would_lose_subsections": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_WOULD_LOSE_SUBSECTIONS",
        "peg_owned_same_chapter": "APPLY.UNCOVERED_BODY_PEG_SAME_CHAPTER_OWNED",
        "peg_owned_label_collision": "APPLY.UNCOVERED_BODY_PEG_LABEL_COLLISION",
        "future_repeal": "APPLY.UNCOVERED_BODY_FUTURE_REPEAL_SKIP",
        "chapter_payload_owned": "APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_OWNED",
    }.get(reason, "APPLY.UNCOVERED_BODY_RECOVERY_SKIPPED")
    detail: dict[str, object] = {
        "message": "Uncovered-body recovery skipped a candidate section",
        "target_section": target_section,
        "target_chapter": target_chapter or "",
        "reason": reason,
    }
    if target_part:
        detail["target_part"] = target_part
    return Finding(
        kind=specific_kind,
        role="observation",
        stage="grafter_uncovered",
        blocking=False,
        source_statute=source_statute,
        detail=detail,
    )


def _uncovered_body_chapter_payload_mixed_finding(
    *,
    source_statute: str,
    target_chapter: str,
    adopted_count: int,
    owned_count: int,
) -> Finding:
    return Finding(
        kind="APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_MIXED",
        role="observation",
        stage="grafter_uncovered",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Covered chapter payload mixed owned child sections with explicit uncovered-body adoptions",
            "target_chapter": target_chapter,
            "adopted_count": adopted_count,
            "owned_count": owned_count,
        },
    )


def _high_uncovered_body_degraded_finding(
    *,
    source_statute: str,
    uncovered_count: int,
    total_units: int,
    uncov_ratio: float,
    confidence: float,
    signals: list[str],
) -> Finding:
    """Build the typed finding for a degraded uncovered-body chapter insert."""
    return Finding(
        kind="COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
        role="obligation",
        stage="coverage_analysis",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": (
                "chapter-level INSERT plan has high uncovered body ratio; "
                "fallback proceeded with explicit degraded confidence"
            ),
            "uncovered_count": uncovered_count,
            "total_units": total_units,
            "uncov_ratio": round(uncov_ratio, 4),
            "confidence": confidence,
            "signals": signals,
        },
    )


def _coverage_ignored_unit_finding(
    *,
    source_statute: str,
    unit_kind: str,
    reason: str,
    observed_label: str | None,
    parent_label: str | None,
    evidence: tuple[str, ...],
) -> Finding:
    return Finding(
        kind="COVERAGE.BODY_UNIT_IGNORED",
        role="observation",
        stage="coverage_analysis",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Body coverage ignored a malformed or unlabeled source unit",
            "unit_kind": unit_kind,
            "reason": reason,
            "observed_label": observed_label or "",
            "parent_label": parent_label or "",
            "evidence": list(evidence),
        },
    )


def _coverage_rejected_claim_finding(
    *,
    source_statute: str,
    reason: str,
    evidence: tuple[str, ...],
) -> Finding:
    return Finding(
        kind="COVERAGE.CLAIM_REJECTED",
        role="observation",
        stage="coverage_analysis",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Body coverage rejected a targetless or unsupported coverage claim",
            "reason": reason,
            "evidence": list(evidence),
        },
    )


def _coverage_unresolved_gap_finding(
    *,
    source_statute: str,
    disposition: str,
    unit_kind: str,
    observed_label: str | None,
    parent_label: str | None,
    evidence: tuple[str, ...],
) -> Finding:
    return Finding(
        kind="COVERAGE.UNRESOLVED_BODY_GAP",
        role="obligation",
        stage="coverage_analysis",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": "Body coverage found an unresolved uncovered unit",
            "disposition": disposition,
            "unit_kind": unit_kind,
            "observed_label": observed_label or "",
            "parent_label": parent_label or "",
            "evidence": list(evidence),
        },
    )


def _expand_johto_section_label_range(start: str, end: str) -> tuple[str, ...]:
    """Expand a johto-mentioned section range into normalized labels.

    Supports:
    - purely numeric ranges, e.g. ``17-21 §``
    - same-base alpha suffix ranges, e.g. ``21 a-21 d §``

    Falls back to returning the normalized endpoints when the range shape is not
    safely expandable.
    """
    start_norm = _norm_num_token(start)
    end_norm = _norm_num_token(end)
    if not start_norm or not end_norm:
        return tuple(label for label in (start_norm, end_norm) if label)

    if start_norm.isdigit() and end_norm.isdigit():
        s_int, e_int = int(start_norm), int(end_norm)
        if 0 < e_int - s_int < 500:
            return tuple(str(i) for i in range(s_int, e_int + 1))
        return (start_norm, end_norm)

    start_match = re.fullmatch(r"(\d+)([a-z])", start_norm)
    end_match = re.fullmatch(r"(\d+)([a-z])", end_norm)
    if start_match and end_match and start_match.group(1) == end_match.group(1):
        start_ord = ord(start_match.group(2))
        end_ord = ord(end_match.group(2))
        if 0 <= end_ord - start_ord < 26:
            base = start_match.group(1)
            return tuple(f"{base}{chr(code)}" for code in range(start_ord, end_ord + 1))

    return (start_norm, end_norm)


def _collect_johto_mentioned_section_labels(johto_text: str) -> set[str]:
    labels: set[str] = set()
    for m in re.finditer(r"(\d+\s*[a-z]?)(?:[-\u2014\u2013\u2015](\d+\s*[a-z]?))?\s*§", johto_text, re.I):
        start = m.group(1)
        end = m.group(2)
        if end:
            labels.update(_expand_johto_section_label_range(start, end))
        else:
            norm = _norm_num_token(start)
            if norm:
                labels.add(norm)
    for m in re.finditer(
        r"((?:\d+\s*[a-z]?(?:[-\u2014\u2013\u2015]\d+\s*[a-z]?)?)"
        r"(?:\s*(?:,|ja|sekä)\s*(?:\d+\s*[a-z]?(?:[-\u2014\u2013\u2015]\d+\s*[a-z]?)?))+)\s*§",
        johto_text,
        re.I,
    ):
        for seg in re.split(r"\s*(?:,|ja|sekä)\s*", m.group(1)):
            seg = seg.strip()
            if not seg:
                continue
            range_match = re.fullmatch(r"(\d+\s*[a-z]?)[-\u2014\u2013\u2015](\d+\s*[a-z]?)", seg, re.I)
            if range_match:
                labels.update(_expand_johto_section_label_range(range_match.group(1), range_match.group(2)))
                continue
            labels.add(_norm_num_token(seg))
    return labels


def _recover_uncovered_body_ops(
    state: "ReplayState",
    ctx: "StatuteContext",
    ops: List[AmendmentOp],
    muutos_tree: etree._Element,
    amendment_id: str,
    future_repeals: Optional[Set["RepealTargetRef"]] = None,
    op_source: Optional[OperationSource] = None,
    new_chapter_labels: Optional[Set[str]] = None,
    failed_ops_out: Optional[List[FailedOp]] = None,
    restructure_plans_out: Optional[List[StructuralTransformPlan]] = None,
    observations_out: Optional[List[Dict[str, object]]] = None,
    findings_out: Optional[List[Finding]] = None,
) -> List[ResolvedOp]:
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
    from lawvm.finland.grafter import RepealTargetRef

    def _heading_text(node: IRNode) -> str:
        heading = next((c for c in node.children if c.kind is IRNodeKind.HEADING), None)
        return " ".join(irnode_to_text(heading).split()).strip().lower() if heading is not None else ""

    def _next_letter_label(label: str) -> Optional[str]:
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

    def _record_skip(
        reason: str,
        label: str,
        amend_chapter_label: Optional[str],
        amend_part_label: Optional[str] = None,
    ) -> None:
        if findings_out is not None:
            findings_out.append(
                _uncovered_body_recovery_skipped_finding(
                    source_statute=amendment_id,
                    target_section=label,
                    target_chapter=amend_chapter_label,
                    target_part=amend_part_label,
                    reason=reason,
                )
            )

    # Build set of sections covered by PEG ops, excluding ops that FAILED
    # during apply_op_ir. Failed ops block uncovered body recovery but
    # didn't actually modify the tree — the fallback should still apply.
    #
    # covered_labels is part+chapter-aware: stores (part, chapter, section)
    # tuples. An op with chapter="" still covers the section in all chapters
    # within the same part, and a truly unscoped op uses part="" / chapter=""
    # as the global wildcard.
    failed_sections: Set[str] = set()
    if failed_ops_out:
        for fop in failed_ops_out:
            if fop.target_unit_kind == "section" and fop.target_section:
                failed_sections.add(_norm_num_token(fop.target_section))
    covered_labels: Set[Tuple[str, str, str]] = set()  # (part, chapter, section)
    covered_chapter_payloads: Set[str] = set()
    chapter_payload_section_dispositions: dict[str, dict[str, int]] = {}
    for op in ops:
        if op.target_unit_kind == "section" and op.target_section:
            label = _norm_num_token(op.target_section)
            if label not in failed_sections:
                ch = _norm_num_token(op.target_chapter) if op.target_chapter else ""
                pt = _norm_num_token(op.target_part) if op.target_part else ""
                pt_arabic = _roman_label_to_arabic(pt) if pt else None
                covered_labels.add((str(pt_arabic) if pt_arabic is not None else pt, ch, label))
        if (
            op.target_unit_kind == "chapter"
            and op.target_section
            and op.op_type in ("REPLACE", "INSERT")
            and not op.target_paragraph
            and not op.target_item
            and not op.target_special
        ):
            # Strip "luku" suffix so the label matches what _process_section_candidate
            # receives from _gap.unit.parent_label (computed via
            # _normalize_chapter_label → removesuffix('luku')).
            covered_chapter_payloads.add(_norm_num_token(op.target_section).removesuffix("luku"))

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
    if findings_out is not None:
        for _ignored in _cov_report.ignored_units:
            findings_out.append(
                _coverage_ignored_unit_finding(
                    source_statute=amendment_id,
                    unit_kind=_ignored.unit_kind,
                    reason=_ignored.reason,
                    observed_label=_ignored.observed_label,
                    parent_label=_ignored.parent_label,
                    evidence=_ignored.evidence,
                )
            )
        for _rejected in _cov_report.rejected_claims:
            findings_out.append(
                _coverage_rejected_claim_finding(
                    source_statute=amendment_id,
                    reason=_rejected.reason,
                    evidence=_rejected.evidence,
                )
            )
        for _gap in _cov_report.obligations:
            findings_out.append(
                _coverage_unresolved_gap_finding(
                    source_statute=amendment_id,
                    disposition=_gap.disposition,
                    unit_kind=_gap.unit.kind,
                    observed_label=_gap.unit.observed_label,
                    parent_label=_gap.unit.parent_label,
                    evidence=_gap.evidence,
                )
            )
    muutos_body = muutos_tree.find(".//{*}body")
    if muutos_body is None:
        return []
    if _cov_report.uncovered_count > 0:
        _replay_print(
            f"  [{amendment_id}] Coverage: {len(_cov_units)} units, "
            f"{len(_cov_claims)} claimed, "
            f"{_cov_report.uncovered_count} uncovered"
        )
    # --- Restructure signal detection + StructuralTransformPlan ---
    # Detect large-restructure amendments: chapter/part inserts + high uncovered ratio.
    # When signals are present, build a typed plan for auditing and future execution.
    _total_units = len(_cov_units)
    _uncov_ratio = _cov_report.uncovered_count / _total_units if _total_units > 0 else 0.0

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
    chapter_payload_owned_sections: set[tuple[str, str, str]] = set()
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
            chapter_payload_owned_sections.add((_unit.part_label, _unit.chapter_label, _unit.label))
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
        # Emit degradation observation when a chapter-level INSERT plan still
        # has a high proportion of uncovered body units.  This surfaces the gap
        # explicitly instead of silently proceeding via permissive fallback.
        _has_chapter_insert_signal = RestructureSignal.CHAPTER_INSERT in _restructure_plan.signals
        _has_high_uncov = RestructureSignal.HIGH_UNCOVERED_BODY in _restructure_plan.signals
        if _has_chapter_insert_signal and _has_high_uncov and observations_out is not None:
            observations_out.append({
                "kind": "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
                "stage": "coverage_analysis",
                "amendment_id": amendment_id,
                "uncovered_count": _cov_report.uncovered_count,
                "total_units": _total_units,
                "uncov_ratio": round(_uncov_ratio, 4),
                "confidence": _restructure_plan.confidence,
                "signals": [s.value for s in _restructure_plan.signals],
            })
            if findings_out is not None:
                findings_out.append(
                    _high_uncovered_body_degraded_finding(
                        source_statute=amendment_id,
                        uncovered_count=_cov_report.uncovered_count,
                        total_units=_total_units,
                        uncov_ratio=_uncov_ratio,
                        confidence=_restructure_plan.confidence,
                        signals=[s.value for s in _restructure_plan.signals],
                    )
                )
            logger.warning(
                "  [%s] COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED: "
                "%d/%d units uncovered (ratio=%.2f, confidence=%.2f) — "
                "chapter-level INSERT plan proceeding with degraded confidence",
                amendment_id, _cov_report.uncovered_count, _total_units,
                _uncov_ratio, _restructure_plan.confidence,
            )
    # --- end restructure signal detection + plan ---
    # --- end typed coverage analysis ---

    has_content_ops = any(op.op_type in ("REPLACE", "INSERT") and op.target_unit_kind == "section" for op in ops)
    # Relax guard when PEG produced chapter-level ops or when the johtolause
    # explicitly mentions muutetaan/lisätään (PEG truncation: parsed kumotaan
    # but missed muutetaan clause). Existing omission + subsec guards still
    # prevent unsafe replacements.
    if not has_content_ops:
        has_chapter_ops = any(
            op.op_type in ("REPLACE", "INSERT") and op.target_unit_kind == "chapter" for op in ops
        )
        if has_chapter_ops:
            has_content_ops = True
        else:
            # Check johtolause from amendment preamble
            johto_el = muutos_tree.find(".//{*}preamble")
            if johto_el is not None:
                johto_text = etree.tostring(johto_el, method="text", encoding="unicode")
                if re.search(r"\bmuutetaan\b|\blisätään\b", johto_text, re.IGNORECASE):
                    has_content_ops = True

    def _is_section_covered(label: str, chapter: str, part: str | None) -> bool:
        """Check if a section label is covered by PEG ops.

        A section is covered if:
        - ("", "", label) is in covered_labels (fully unscoped op)
        - (part, "", label) is in covered_labels (part-scoped op without chapter)
        - (part, chapter, label) is in covered_labels (exact part/chapter match)
        """
        norm_part = _norm_num_token(part) if part else ""
        part_arabic = _roman_label_to_arabic(norm_part) if norm_part else None
        norm_part = str(part_arabic) if part_arabic is not None else norm_part
        if ("", "", label) in covered_labels:
            return True
        if norm_part and (norm_part, "", label) in covered_labels:
            return True
        if chapter and (norm_part, chapter, label) in covered_labels:
            return True
        return False

    def _is_future_repealed(label: str, chapter: Optional[str]) -> bool:
        """Check if this section will be REPEALed by a later amendment.

        Matches:
        - ('P', label, None)          — section repealed without chapter context
        - ('P', label, chapter)       — section in this chapter explicitly repealed

        NOTE: whole-chapter future repeals ('L', chapter, None) are intentionally
        NOT matched here.  Skipping a chapter's sections because the chapter will
        be future-repealed causes the chapter to become empty; subsequent RENUMBER
        ops then eliminate it before the actual REPEAL op can fire, making the
        REPEAL fail with "chapter not found".  Let the REPEAL op handle removal;
        only skip individually-targeted sections.
        """
        if future_repeals is None:
            return False
        # Section-level repeal without chapter context
        if RepealTargetRef.section(label) in future_repeals:
            return True
        # Section-level repeal with matching chapter
        if chapter and RepealTargetRef.section(label, chapter) in future_repeals:
            return True
        return False

    # Pre-extract section labels explicitly mentioned in the preamble so
    # uncovered-body fallback can stay scoped to the cited statute surface.
    johto_mentioned_labels: Set[str] = set()
    johto_mentioned_new_chapters: Set[str] = set()
    johto_mentioned_replaced_chapters: Set[str] = set()
    moved_section_destinations: dict[str, str] = {}
    relabel_destination_sections: Set[tuple[str, str, str]] = set()
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
        relabel_destination_sections.add((dest_part, dest_chapter, dest_section))
    johto_el = muutos_tree.find(".//{*}preamble")
    if johto_el is not None:
        johto_text = etree.tostring(johto_el, method="text", encoding="unicode")
        # Single-item and range section references (e.g. "18 a §", "17―21 §").
        # The label group uses \d+\s*[a-z]? to capture space-separated
        # letter suffixes (e.g. "18 a") as well as adjacent ones ("18a").
        # The character class [-\u2014\u2013\u2015] covers hyphen, em-dash,
        # en-dash, and horizontal bar (U+2015, used in Finlex XML ranges).
        johto_mentioned_labels.update(_collect_johto_mentioned_section_labels(johto_text))
        # Extract newly-inserted chapter references: require "lisätään" or "uusi"
        # as a mandatory prefix to distinguish new chapter insertions from
        # existing chapter modifications.  Sections inside these chapters are
        # automatically allowed because the chapter creation implies all its
        # sections.
        #
        # Matches: "lisätään uusi 2 b luku", "lisätään 2 b luku", "uusi 2 b luku"
        # Also matches range forms: "uusi 47―49 luku", "uusi 47-49 luku"
        # Does NOT match: "5 luvun 3 §" (genitive), "muutetaan 5 luku" (modify)
        _DASH_CHARS = r"[-\u2013\u2014\u2015]"  # hyphen, en-dash, em-dash, horizontal bar
        for m in re.finditer(
            r"(?:lisätään\s+(?:lakiin\s+)?|uusi\s+)"
            r"(\d+\s*[a-z]?)"
            r"(?:\s*" + _DASH_CHARS + r"\s*(\d+\s*[a-z]?))?"
            r"\s+luku",
            johto_text,
            re.I,
        ):
            start_label = _norm_num_token(m.group(1)).removesuffix("luku")
            end_label = _norm_num_token(m.group(2)).removesuffix("luku") if m.group(2) else None
            if start_label and end_label and start_label.isdigit() and end_label.isdigit():
                s_int, e_int = int(start_label), int(end_label)
                if 0 < e_int - s_int < 100:
                    for i in range(s_int, e_int + 1):
                        johto_mentioned_new_chapters.add(str(i))
            elif start_label:
                johto_mentioned_new_chapters.add(start_label)
        # Move clauses can also create chapter ownership for the destination
        # chapter, even when the wording uses ``siirretään`` rather than
        # ``lisätään``.  Without this, a moved section inside a relocated
        # chapter can be mistaken for a family-base replacement and get
        # reanchored into an older chapter that happens to share the label.
        for m in re.finditer(
            r"\bsiirretään\b[^§\n]{0,200}?(?:lakiin\s+)?(\d+\s*[a-z]?)\s+lukuun",
            johto_text,
            re.I,
        ):
            dest_chapter = _norm_num_token(m.group(1)).removesuffix("luku")
            if dest_chapter:
                owned_chapter_labels.add(dest_chapter)
        for m in re.finditer(
            r"(\d+\s*[a-z]?)\s*§[^§\n]{0,120}?\bsiirretään\b[^§\n]{0,200}?(?:lakiin\s+)?(\d+\s*[a-z]?)\s+lukuun",
            johto_text,
            re.I,
        ):
            source_label = _norm_num_token(m.group(1))
            dest_chapter = _norm_num_token(m.group(2)).removesuffix("luku")
            if source_label and dest_chapter:
                moved_section_destinations[source_label] = dest_chapter
        # Extract whole-chapter replacements: "muutetaan X luku" means all
        # sections within chapter X are being replaced and should be allowed
        # through the johto guard.
        #
        # Matches: "muutetaan 45 luku", "muutetaan ... 21, 25, 34 ja 38 luku"
        # Also matches range forms: "muutetaan ... 47―49 luku"
        # Does NOT match: "5 luvun 3 §" (genitive reference)
        #
        # Strategy: when the preamble contains "muutetaan", find all
        # <number-list> luku patterns (nominative, not genitive "luvun")
        # and collect chapters.  The chapter list may be far from the
        # "muutetaan" verb due to intervening section references.
        if re.search(r"\bmuutetaan\b", johto_text, re.I):
            # Two-step approach to avoid catastrophic regex backtracking
            # (the previous nested-quantifier pattern caused >2min hangs
            # on statutes like 1982/182, 2011/415, etc.):
            # Step 1: find every "luku" word boundary position.
            # Step 2: scan the preceding text for chapter numbers.
            _NUM_PAT = re.compile(
                r"(\d+\s*(?:[a-z](?![a-z]))?)(?:\s*" + _DASH_CHARS + r"\s*(\d+\s*(?:[a-z](?![a-z]))?))?",
                re.I,
            )
            for luku_m in re.finditer(r"\bluku\b", johto_text, re.I):
                # Look at up to 200 chars before "luku" for number list
                start = max(0, luku_m.start() - 200)
                prefix = johto_text[start : luku_m.start()]
                # Find all number tokens in the prefix; only keep those
                # at the tail (not separated by §, luvun, or other structure)
                # by scanning from the end.
                for rm in _NUM_PAT.finditer(prefix):
                    # Only accept numbers that are close to "luku" — reject
                    # if there's a § or "luvun" between the number and "luku"
                    between = prefix[rm.end() :]
                    if re.search(r"§|luvun", between, re.I):
                        continue
                    start_ch = _norm_num_token(rm.group(1)).removesuffix("luku")
                    end_ch = _norm_num_token(rm.group(2)).removesuffix("luku") if rm.group(2) else None
                    if start_ch and end_ch and start_ch.isdigit() and end_ch.isdigit():
                        s_int, e_int = int(start_ch), int(end_ch)
                        if 0 < e_int - s_int < 100:
                            for i in range(s_int, e_int + 1):
                                johto_mentioned_replaced_chapters.add(str(i))
                    elif start_ch:
                        johto_mentioned_replaced_chapters.add(start_ch)
    owned_chapter_labels.update(johto_mentioned_new_chapters)

    def _label_allowed_by_johto(label: str, chapter: Optional[str] = None) -> bool:
        if not johto_mentioned_labels:
            return True
        # If the section's chapter is a newly-inserted chapter from the johtolause,
        # allow all its sections (the chapter creation implies all member sections).
        if chapter and chapter in owned_chapter_labels:
            return True
        # If the section's chapter is a whole-chapter replacement ("muutetaan X luku"),
        # allow all its sections — the chapter replacement implies all member sections.
        if chapter and chapter in johto_mentioned_replaced_chapters:
            return True
        if label in johto_mentioned_labels:
            return True
        base_label = re.match(r"^(\d+)", label)
        return bool(base_label and base_label.group(1) in johto_mentioned_labels)

    def _make_uncovered_rop(
        op_type: OpType,
        target_label: str,
        target_chapter: Optional[str],
        target_part: Optional[str],
        muutos_ir: IRNode,
        op_id: str,
    ) -> ResolvedOp:
        """Build a ResolvedOp for an uncovered-body section operation."""
        am_op = AmendmentOp(
            op_id=op_id,
            op_type=op_type,
            target_section=target_label,
            target_unit_kind="section",
            target_chapter=target_chapter,
            target_part=target_part,
            source_statute=amendment_id,
            uncovered_body_recovery=True,
        )
        rop = ResolvedOp.from_amendment_op(
            am_op,
            muutos_ir=muutos_ir,
            cross_ir=None,
            target_unit_kind="section",
            target_norm=target_label,
            target_chapter=target_chapter,
            payload_completeness=_uncovered_section_payload_completeness(
                op_type=op_type,
                muutos_ir=muutos_ir,
            ),
            op_source=op_source,
            target_address=LegalAddress(
                path=(
                    ((("part", target_part),) if target_part else ())
                    + ((("chapter", target_chapter),) if target_chapter else ())
                    + (("section", target_label),)
                )
            ),
        )
        return rop

    result: List[ResolvedOp] = []
    seen_recovery_findings: Set[tuple[str, str, str, str, str]] = set()
    recovered_section_keys: Set[tuple[str, str]] = set()

    def _append_recovered_rop(rop: ResolvedOp) -> None:
        target_norm, target_chapter, _target_part, _target_paragraph, _target_item, _target_special = (
            rop.resolved_target_scope
        )
        recovered_section_keys.add((_norm_num_token(target_norm), _norm_num_token(target_chapter or "")))
        result.append(rop)
        if findings_out is None:
            return
        target_part = rop.resolved_target_scope[2]
        finding = _uncovered_body_recovery_finding(
            op_id=rop.op_id,
            source_statute=amendment_id,
            target_unit_kind=rop.target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
        )
        if finding is None:
            return
        key = (
            str(finding.kind or ""),
            str(target_norm or ""),
            str(target_chapter or ""),
            str(target_part or ""),
            str(rop.op_id or ""),
        )
        if key in seen_recovery_findings:
            return
        seen_recovery_findings.add(key)
        findings_out.append(finding)

    def _process_section_candidate(
        sec: etree._Element,
        label: str,
        amend_chapter_label: Optional[str],
    ) -> None:
        """Process one candidate uncovered section and append to result if warranted.

        Called from the coverage-driven primary path.  Mutates
        ``result`` and ``covered_labels`` in place.
        """
        def _xml_part_label(section_el: etree._Element) -> Optional[str]:
            parent = section_el.getparent()
            while parent is not None:
                if _tag(parent) == "part":
                    num_el = parent.find("{*}num")
                    if num_el is not None and num_el.text:
                        part_label = _norm_num_token(num_el.text).removesuffix("osa")
                        arabic = _roman_label_to_arabic(part_label)
                        part_label = str(arabic) if arabic is not None else part_label
                        return part_label or None
                parent = parent.getparent()
            return None

        def _part_label_from_path(path: tuple[tuple[str, str], ...] | None) -> Optional[str]:
            if not path:
                return None
            return next((lbl for kind, lbl in path if kind == "part"), None)

        import os as _os

        _DEBUG_RECOVERY = _os.environ.get("LAWVM_DEBUG_RECOVERY") == "1"
        if _DEBUG_RECOVERY:
            print(f"  [DBG] _process_section_candidate: label={label!r}, chapter={amend_chapter_label!r}")

        amend_part_label = _xml_part_label(sec)

        recovered_key = (_norm_num_token(label), _norm_num_token(amend_chapter_label or ""))
        if recovered_key in recovered_section_keys:
            if _DEBUG_RECOVERY:
                print(f"  [DBG]  -> SKIP: already recovered {label!r} in chapter {amend_chapter_label!r}")
            _record_skip("duplicate_recovered_candidate", label, amend_chapter_label)
            return

        move_destination = moved_section_destinations.get(label)
        if move_destination and amend_chapter_label != move_destination:
            if _DEBUG_RECOVERY:
                print(
                    f"  [DBG]  -> SKIP: label {label!r} moved to chapter {move_destination!r},"
                    f" not {amend_chapter_label!r}"
                )
            _record_skip("moved_destination_mismatch", label, amend_chapter_label)
            return

        if amend_chapter_label and (
            (amend_part_label or "", amend_chapter_label, label) in relabel_destination_sections
        ):
            if _DEBUG_RECOVERY:
                print(
                    "  [DBG]  -> SKIP: section "
                    f"{label!r} in chapter {amend_chapter_label!r} already owned by same-wave relabel destination"
                )
            _record_skip(
                "same_wave_relabel_destination_owned",
                label,
                amend_chapter_label,
                amend_part_label,
            )
            return

        # Body-pairing guard: reject sections that are foreign-statute,
        # unmatched, or REPEAL-claimed.  This prevents the bug where a
        # repealing amendment's own body sections (with same labels as the
        # repealed statute) get inserted as content.
        if _bp_assignments and not should_use_body_section(label, amend_chapter_label or "", _bp_assignments):
            if _DEBUG_RECOVERY:
                print(f"  [DBG]  -> SKIP: body-pairing guard rejected {label!r}")
            logger.debug(
                "  [%s] uncovered SKIP %s § — body-pairing guard (foreign/unmatched/repeal)",
                amendment_id,
                label,
            )
            _record_skip("body_pairing_guard", label, amend_chapter_label)
            return

        # A whole-chapter INSERT/REPLACE op already owns the chapter payload.
        # Its child sections should not be double-counted as uncovered body
        # operations just because they are visible in the amendment body.
        #
        # Exception: large restructure amendments sometimes produce CHAPTER INSERT
        # ops alongside standalone SECTION ops for the new chapter's sections.
        # apply_structure_ops filters those sections from the chapter INSERT payload
        # (to avoid duplicating standalone ops), but if the standalone section op
        # had no chapter context it may land in the wrong chapter.  When the section
        # is still absent from master after all PEG ops ran, adopt it explicitly.
        if (
            amend_chapter_label
            and (amend_part_label or "", amend_chapter_label, label) in chapter_payload_owned_sections
        ):
            if _DEBUG_RECOVERY:
                print(
                    f"  [DBG]  -> SKIP: section {label!r} owned by chapter payload"
                    f" in chapter {amend_chapter_label!r}"
                )
            # Check whether the section was actually placed into the new chapter.
            _adopt_path = state.find_section_path(label, amend_chapter_label, amend_part_label)
            if _adopt_path is None:
                # Section still absent from the new chapter — the chapter INSERT op
                # filtered it (standalone_section_targets guard) or never included it.
                # Adopt it now so it lands in the correct chapter.
                if not _is_future_repealed(label, amend_chapter_label):
                    _adopt_sec_ir = fi_xml_to_ir_node(sec, _fi_label_postprocessor)
                    covered_labels.add((amend_part_label or "", amend_chapter_label, label))
                    _append_recovered_rop(
                        _make_uncovered_rop(
                            "INSERT",
                            label,
                            amend_chapter_label,
                            amend_part_label,
                            _adopt_sec_ir,
                            f"uncov_chapter_adopt_{label}",
                        )
                    )
                    chapter_payload_section_dispositions.setdefault(
                        amend_chapter_label, {"adopted": 0, "owned": 0}
                    )["adopted"] += 1
                    if _DEBUG_RECOVERY:
                        print(f"  [DBG]  -> ADOPT into chapter {amend_chapter_label!r}: INSERT {label!r}")
                else:
                    _record_skip("future_repeal", label, amend_chapter_label)
            else:
                covered_labels.add((amend_part_label or "", amend_chapter_label, label))
                chapter_payload_section_dispositions.setdefault(
                    amend_chapter_label, {"adopted": 0, "owned": 0}
                )["owned"] += 1
                _record_skip("chapter_payload_owned", label, amend_chapter_label)
            return

        # Find in state.ir (READ-ONLY — no mutations here)
        existing_path = state.find_section_path(label, amend_chapter_label)
        if existing_path is None and amend_chapter_label:
            # Only fall back to un-scoped lookup when the label is unique
            # across chapters.  When duplicate labels exist (e.g. Vesilaki
            # where every chapter has "1 §"), the un-scoped lookup resolves
            # to a random chapter's section, producing wrong-chapter content
            # application (Pattern E cross-chapter collision).
            #
            # Also skip the fallback when the chapter is newly inserted by this
            # amendment.  A new chapter's sections do not exist anywhere in the
            # master yet; the un-scoped fallback would find a same-numbered
            # section in an existing chapter and trigger a false cross-chapter
            # hit, silently dropping the INSERT.
            if label not in state.duplicate_section_labels and amend_chapter_label not in owned_chapter_labels:
                existing_path = state.find_section_path(label)

        # Check cross-chapter mismatch
        cross_chapter = False
        if existing_path is not None and amend_chapter_label is not None:
            path_chapter = next((lbl for k, lbl in existing_path if k == "chapter"), None)
            if path_chapter is None or path_chapter != amend_chapter_label:
                cross_chapter = True
        # When the amendment body has NO chapter context but the label is
        # duplicated across chapters, the un-scoped lookup resolved to an
        # arbitrary chapter.  Treat this as an ambiguous cross-chapter hit
        # to prevent replacing the wrong chapter's section (Pattern E).
        if existing_path is not None and amend_chapter_label is None and label in state.duplicate_section_labels:
            cross_chapter = True

        sec_ir = fi_xml_to_ir_node(sec, _fi_label_postprocessor)

        if existing_path is not None:
            existing = _tops.resolve(state.ir, existing_path)
            if existing is None:
                # Path is stale (tree was mutated by earlier ops in this batch);
                # treat as new insert.
                existing_path = None
            else:
                existing_heading = _heading_text(existing)
                amend_heading = _heading_text(sec_ir)
                if (
                    existing_heading.startswith("voimaantulo")
                    and amend_heading
                    and not amend_heading.startswith("voimaantulo")
                ):
                    # TODO (architecture): voimaantulo-relabel is a pre-computation
                    # that requires knowing which insert_label to use, which in turn
                    # needs the current sibling list.  Since the sibling list changes
                    # after each insert in the result list, this case needs sequential
                    # state-dependent resolution and cannot be purely pre-collected.
                    # For now, compute the insert_label against the initial state
                    # (conservative: if the sibling was already inserted by an earlier
                    # op in result, the insert_label may be off by one letter).
                    parent_path = existing_path[:-1]
                    parent = _tops.resolve(state.ir, parent_path) if parent_path else state.ir
                    section_siblings = [c for c in parent.children if c.kind is IRNodeKind.SECTION] if parent is not None else []
                    insert_label: Optional[str] = None
                    if existing in section_siblings:
                        existing_idx = section_siblings.index(existing)
                        if existing_idx > 0:
                            insert_label = _next_letter_label(section_siblings[existing_idx - 1].label or "")
                    if insert_label and state.find_section_path(insert_label, amend_chapter_label) is None:
                        inserted_sec = _relabel_section_ir(sec_ir, insert_label)
                        covered_labels.add((amend_part_label or "", amend_chapter_label or "", label))
                        _append_recovered_rop(
                            _make_uncovered_rop(
                                "INSERT",
                                insert_label,
                                amend_chapter_label,
                                amend_part_label or _part_label_from_path(existing_path),
                                inserted_sec,
                                f"uncovered_insert_{insert_label}",
                            )
                        )
                        return
                if not _label_allowed_by_johto(label, amend_chapter_label):
                    _record_skip("johto_guard", label, amend_chapter_label)
                    return
                # --- Past-repeal guard ---
                # If the target section is already a repeal placeholder (from
                # a PAST amendment), do not override it with body-coverage
                # REPLACE.  The section was deliberately repealed; the body
                # content in this amendment either targets a different
                # chapter or is stale/mis-targeted.
                #
                # Exception: tilalle-range INSERT ops explicitly restore a
                # previously-repealed section slot.  When the PEG-compiled ops
                # include an INSERT for this (chapter, section) label pair, the
                # amendment deliberately targets this repeal placeholder and
                # the guard must be bypassed so the new content can replace it.
                # Whole-chapter-replace flag: when the johtolause explicitly
                # says "muutetaan X luku", the amendment body is authoritative
                # for ALL sections in that chapter.  Two guards below are
                # relaxed under this flag:
                # 1. past-repeal guard — a repealed slot may be reinstated.
                # 2. would_lose_subsections — the chapter restructure is the
                #    new truth; a lower subsection count is intentional.
                _whole_ch_replace = bool(
                    amend_chapter_label
                    and amend_chapter_label in johto_mentioned_replaced_chapters
                )
                if existing.attrs.get("lawvm_repeal_placeholder") == "1":
                    _has_insert_op_for_label = any(
                        op.op_type == "INSERT"
                        and op.target_unit_kind == "section"
                        and op.target_section
                        and _norm_num_token(op.target_section) == label
                        and (
                            not op.target_chapter
                            or not amend_chapter_label
                            or _norm_num_token(op.target_chapter) == amend_chapter_label
                        )
                        for op in ops
                    )
                    if not _has_insert_op_for_label and not _whole_ch_replace:
                        covered_labels.add((amend_part_label or "", amend_chapter_label or "", label))
                        _record_skip("past_repeal_placeholder_guard", label, amend_chapter_label)
                        return
                    # Tilalle INSERT or whole-chapter replace: fall through to
                    # REPLACE logic below so the repeal placeholder is replaced
                    # with the new content.
                    logger.debug(
                        "  [%s] uncovered: bypassing past-repeal guard for %s § (%s)",
                        amendment_id,
                        label,
                        "tilalle INSERT" if _has_insert_op_for_label else "whole-chapter replace",
                    )
                amend_subsec_count = len([c for c in sec_ir.children if c.kind is IRNodeKind.SUBSECTION])
                master_subsec_count = len([c for c in existing.children if c.kind is IRNodeKind.SUBSECTION])
                would_lose_subsections = amend_subsec_count < master_subsec_count
                has_omissions = _has_section_omissions_ir(sec_ir)
                # For whole-chapter replacements, the amendment body is
                # authoritative even when the section shrinks subsection count.
                effective_would_lose = would_lose_subsections and not _whole_ch_replace
                can_replace = has_content_ops and not has_omissions and not cross_chapter and not effective_would_lose
                import os as _os

                if _os.environ.get("LAWVM_DEBUG_RECOVERY") == "1":
                    print(
                        f"  [DBG]  existing, can_replace={can_replace}, has_content_ops={has_content_ops}, has_omissions={has_omissions}, cross_chapter={cross_chapter}, would_lose={would_lose_subsections}, whole_ch_replace={_whole_ch_replace}, amend_ss={amend_subsec_count}, master_ss={master_subsec_count}"
                    )
                if can_replace:
                    if _os.environ.get("LAWVM_DEBUG_RECOVERY") == "1":
                        print(f"  [DBG]  -> REPLACE op: label={label!r}, chapter={amend_chapter_label!r}")
                    covered_labels.add((amend_part_label or "", amend_chapter_label or "", label))
                    _append_recovered_rop(
                        _make_uncovered_rop(
                            "REPLACE",
                            label,
                            amend_chapter_label,
                            amend_part_label or _part_label_from_path(existing_path),
                            sec_ir,
                            f"uncovered_replace_{label}",
                        )
                    )
                elif (
                    has_content_ops
                    and has_omissions
                    and not cross_chapter
                    and any(_is_omission_ir(c) for c in sec_ir.children)
                ):
                    # Omission at section level — try merging amendment into
                    # master.  When would_lose_subsections is True (amendment
                    # has fewer subsections+omissions than master), the merge
                    # function expands each omission to cover multiple master
                    # subsections.  The post-merge guards below still reject
                    # actual subsection loss or text corruption.
                    merged = _merge_section_with_omission_ir(existing, sec_ir)
                    if merged is not None:
                        merged_subsec_count = len([c for c in merged.children if c.kind is IRNodeKind.SUBSECTION])
                        # Guards: merged must have at least as many subsections
                        # as master (allows additions), must not lose significant
                        # text content, and must not have duplicate subsection
                        # labels (merge corruption).
                        master_text = irnode_to_text(existing)
                        merged_text = irnode_to_text(merged)
                        text_ratio = len(merged_text) / len(master_text) if master_text else 1.0
                        merged_labels = [c.label for c in merged.children if c.kind is IRNodeKind.SUBSECTION and c.label]
                        has_dup_labels = len(merged_labels) != len(set(merged_labels))
                        if merged_subsec_count >= master_subsec_count and text_ratio >= 0.75 and not has_dup_labels:
                            covered_labels.add((amend_part_label or "", amend_chapter_label or "", label))
                            # Pass the pre-merged IR as the payload so apply_op
                            # performs replace_at with the already-merged node.
                            _append_recovered_rop(
                                _make_uncovered_rop(
                                    "REPLACE",
                                    label,
                                    amend_chapter_label,
                                    amend_part_label or _part_label_from_path(existing_path),
                                    merged,
                                    f"uncovered_merge_{label}",
                                )
                            )
                            return
                        if merged_subsec_count < master_subsec_count:
                            _record_skip("omission_merge_would_lose_subsections", label, amend_chapter_label)
                        elif text_ratio < 0.75:
                            _record_skip("omission_merge_low_text_ratio", label, amend_chapter_label)
                        elif has_dup_labels:
                            _record_skip("omission_merge_duplicate_subsection_labels", label, amend_chapter_label)
                    else:
                        _record_skip("omission_merge_failed", label, amend_chapter_label)
                    covered_labels.add((amend_part_label or "", amend_chapter_label or "", label))
                else:
                    if cross_chapter:
                        _record_skip("cross_chapter_existing_target", label, amend_chapter_label)
                    elif not has_content_ops:
                        _record_skip("no_content_ops", label, amend_chapter_label)
                    elif effective_would_lose:
                        _record_skip("would_lose_subsections", label, amend_chapter_label)
                    covered_labels.add((amend_part_label or "", amend_chapter_label or "", label))
                return

        if not _label_allowed_by_johto(label, amend_chapter_label):
            _record_skip("johto_guard", label, amend_chapter_label)
            return

        # New section — INSERT.  Use find_family to place in the same
        # chapter as the numeric base (e.g. 39a → chapter of 39).
        # When no chapter context from amendment, only use find_family
        # if the base label is unique (not in multiple chapters).

        # Guard: skip this INSERT if a later amendment will REPEAL this
        # section (or its containing chapter).  Inserting such a section
        # would create spurious content that never appears in the oracle
        # consolidated output.  We only apply this guard to NEW inserts
        # (existing_path is None); replacements of sections that are later
        # repealed are allowed because the repeal placeholder needs
        # well-formed content to reference.
        if _is_future_repealed(label, amend_chapter_label):
            if DEBUG:
                _replay_print(f"  [{amendment_id}] uncovered SKIP INSERT {label} § — future repeal")
            covered_labels.add((amend_part_label or "", amend_chapter_label or "", label))
            _record_skip("future_repeal", label, amend_chapter_label)
            return

        # Family-chapter override: if the amendment placed this section in a new
        # chapter (amend_chapter_label) but the section's numeric-base sibling
        # lives in an UNRELATED existing chapter, use the existing chapter.
        #
        # Example: amendment puts §32a in new chapter 4d (as a container),
        # but §32 lives in chapter 7 → correct target_chapter is "7", not "4d".
        #
        # NOT applied when:
        # 1. The amendment chapter is a sub-chapter of the family's chapter
        #    (e.g., §14a in chapter "4a": §14 is in chapter "4", so "4a" is a
        #    direct sub-chapter of "4" — keep "4a").
        # 2. The family base section is being REPEALED by THIS amendment
        #    (the new chapter placement is intentional).
        # 3. The amendment chapter was NEWLY INSERTED by this amendment (in
        #    johto_mentioned_new_chapters).  A new chapter owns all its sections;
        #    redirecting them to an existing chapter's family base is wrong.
        effective_chapter = amend_chapter_label
        effective_part = amend_part_label
        chapter_is_new = False
        if amend_chapter_label:
            if new_chapter_labels is not None:
                chapter_is_new = amend_chapter_label in new_chapter_labels
            else:
                chapter_is_new = amend_chapter_label not in owned_chapter_labels

        if amend_chapter_label and chapter_is_new:
            # First try to find family base (numeric base) within the same chapter.
            # If not found (base doesn't exist in this chapter), fall back to
            # un-scoped search. This prevents finding the wrong chapter's base
            # when a label exists in multiple chapters.
            family_path = _tops.find_family(
                state.ir, "section", label, scope_kind="chapter", scope_label=amend_chapter_label
            )
            if family_path is None:
                # Base doesn't exist in amendment's chapter; try any chapter
                family_path = _tops.find_family(state.ir, "section", label)
            if family_path is not None:
                family_chapter = next((lbl for k, lbl in family_path if k == "chapter"), None)
                family_part = _part_label_from_path(family_path)
                if family_chapter and family_chapter != amend_chapter_label:
                    base_match = re.match(r"^(\d+)[a-z]*$", label)
                    family_base_label = base_match.group(1) if base_match else None
                    family_base_repealed = (
                        any(
                            op.op_type == "REPEAL"
                            and op.target_unit_kind == "section"
                            and op.target_section
                            and _norm_num_token(op.target_section) == family_base_label
                            and not op.target_paragraph
                            and not op.target_item
                            and not op.target_special
                            for op in ops
                        )
                        if family_base_label
                        else False
                    )
                    if not family_base_repealed:
                        amend_ch_base = re.match(r"^(\d+)", amend_chapter_label)
                        is_sub_chapter = amend_ch_base is not None and amend_ch_base.group(1) == family_chapter
                        if not is_sub_chapter:
                            effective_chapter = family_chapter
                            effective_part = family_part
                            logger.debug(
                                "  [%s] uncovered INSERT %s: overriding chapter %s→%s"
                                " (family base in unrelated existing chapter)",
                                amendment_id,
                                label,
                                amend_chapter_label,
                                family_chapter,
                            )

        covered_labels.add((amend_part_label or "", effective_chapter or "", label))
        # Emit INSERT ResolvedOp.  apply_op will redo the family-anchor
        # lookup against the live (post-prior-insert) state, so placement
        # is correct even when multiple new sections are inserted in sequence.
        _append_recovered_rop(
            _make_uncovered_rop(
                "INSERT",
                label,
                effective_chapter,
                effective_part,
                sec_ir,
                f"uncovered_insert_{label}",
            )
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
            _record_skip("peg_owned_same_chapter", _gap_label, _gap_chapter)
            continue
        # Also skip when PEG already owns the same section label in a different
        # chapter. In that case the body chapter is stale/misleading, and
        # uncovered-body recovery must not manufacture a duplicate same-labeled
        # section under the body's chapter.
        if _gap_label in _peg_targeted_labels:
            _record_skip("peg_owned_label_collision", _gap_label, _gap_chapter)
            continue
        _process_section_candidate(cast(etree._Element, _sec_el), _gap_label, _gap_chapter)

    # --- Dual-run fallback: old ad-hoc section scan (promoted to always-on) ---
    # Previously feature-flagged behind LAWVM_DUAL_UNCOVERED=1.
    # Regression hunting confirmed 3 improvements, 0 regressions when enabled.
    # Key wins: 1990/650 -2.80pp, 1978/38 -0.26pp, 1993/1055 -0.24pp.
    # See notes/PRO_RESPONSE3_4_regression_hunting.md §2.
    if True:
        # Two guard sets:
        # - _result_labels: bare labels from already-resolved ops — prevents
        #   the dual-run from duplicating coverage-driven recovery (which may
        #   have resolved to a different chapter than the body XML's nesting).
        # - _peg_ch_labels: chapter-qualified labels from PEG-compiled ops —
        #   prevents whole-section clobber of fine-grained subsection/item ops,
        #   but only for the SAME chapter (fixes namespace collision where
        #   chapter 2/§1 would incorrectly block chapter 7/§1 recovery).
        _result_labels: Set[str] = set()
        for _rop in result:
            if _rop.target_unit_kind == "section" and _rop.target_norm:
                _result_labels.add(_rop.target_norm)
        _peg_ch_labels: Set[Tuple[Optional[str], str]] = set()
        _peg_labels: Set[str] = set()
        for _op in ops:
            if _op.target_unit_kind == "section" and _op.target_section:
                _norm_label = _norm_num_token(_op.target_section)
                _peg_ch_labels.add((_op.target_chapter, _norm_label))
                _peg_labels.add(_norm_label)

        for _sec in muutos_body.findall(".//{*}section"):
            _num_el = _sec.find("{*}num")
            if _num_el is None or not _num_el.text:
                continue
            _raw = _num_el.text.strip()
            # Some malformed Finland sources encode a new chapter heading as a
            # section like "16 b luku". Body coverage already treats these as
            # chapter markers; the legacy ad-hoc uncovered-section sweep must
            # not resurrect them as bogus section inserts.
            if _norm_num_token(_raw).endswith("luku"):
                continue
            _ad_label = _norm_num_token(re.sub(r"\s*§.*$", "", _raw).strip())
            if not _ad_label:
                continue
            _ad_ch_parent = _sec.getparent()
            _ad_ch: Optional[str] = None
            if _ad_ch_parent is not None and _tag(_ad_ch_parent) == "chapter":
                _cnum_el = _ad_ch_parent.find("{*}num")
                if _cnum_el is not None and _cnum_el.text:
                    _ad_ch = _norm_num_token(_cnum_el.text).removesuffix("luku")
            if _ad_label in _result_labels:
                continue  # Already resolved by coverage-driven path
            if (_ad_ch, _ad_label) in _peg_ch_labels:
                continue  # PEG-compiled ops target this section in the same chapter
            if _ad_label in _peg_labels:
                continue  # PEG already owns this section label in another chapter
            _ad_part: Optional[str] = None
            _part_parent = _ad_ch_parent.getparent() if _ad_ch_parent is not None else None
            while _part_parent is not None:
                if _tag(_part_parent) == "part":
                    _pnum_el = _part_parent.find("{*}num")
                    if _pnum_el is not None and _pnum_el.text:
                        _part_norm = _norm_num_token(_pnum_el.text).removesuffix("osa")
                        _part_arabic = _roman_label_to_arabic(_part_norm)
                        _ad_part = str(_part_arabic) if _part_arabic is not None else (_part_norm or None)
                    break
                _part_parent = _part_parent.getparent()
            if _ad_ch and (_ad_part or "", _ad_ch, _ad_label) in chapter_payload_owned_sections:
                if ((_ad_part or ""), _ad_ch, _ad_label) in covered_labels:
                    continue
                if state.find_section_path(_ad_label, _ad_ch, _ad_part) is not None:
                    covered_labels.add((_ad_part or "", _ad_ch, _ad_label))
                    _record_skip("chapter_payload_owned", _ad_label, _ad_ch)
                    continue
            if _is_section_covered(_ad_label, _ad_ch or "", _ad_part):
                continue
            # Check voimaantulo/provenance via heading (mirrors existing noise filter)
            _ad_heading = ""
            _ad_heading_el = _sec.find("{*}heading")
            if _ad_heading_el is not None:
                _ad_heading = " ".join("".join(str(_t) for _t in _ad_heading_el.itertext()).split()).lower()
            _is_nonoperative = any(
                _ad_heading.startswith(p)
                for p in ("voimaantulo", "siirtymä", "kumottavat", "kumoaminen", "soveltaminen", "voimassaolo")
            )
            # When there is no heading, also inspect text content: a section whose
            # first content paragraph starts with "Tällä lailla/asetuksella/päätöksellä
            # kumotaan" is the amending act's own repeal provision (e.g. 2015/640 §1 that
            # repeal-lists sections from 1994/1466).  It must not be grafted into the base
            # act as a replacement for the identically-numbered section.
            if not _is_nonoperative and not _ad_heading:
                for _ad_sub in _sec.iter():
                    if _tag(_ad_sub) in ("p", "content"):
                        _ad_sub_text = " ".join(str(t) for t in _ad_sub.itertext()).split()
                        _ad_sub_lower = " ".join(_ad_sub_text).lower()
                        if re.match(
                            r"tällä\s+(?:lailla|asetuksella|päätöksellä|säädöksellä)\s+kumotaan\b",
                            _ad_sub_lower,
                        ):
                            _is_nonoperative = True
                        break
            if not _is_nonoperative:
                _replay_print(
                    f"  [{amendment_id}] Dual-run ad-hoc: uncovered section {_ad_label!r}"
                    f"{' (ch=' + _ad_ch + ')' if _ad_ch else ''} — not in coverage result"
                )
                _process_section_candidate(_sec, _ad_label, _ad_ch)
    # --- end dual-run fallback ---

    if findings_out is not None:
        for chapter_label, counts in sorted(chapter_payload_section_dispositions.items()):
            adopted_count = counts.get("adopted", 0)
            owned_count = counts.get("owned", 0)
            if adopted_count and owned_count:
                findings_out.append(
                    _uncovered_body_chapter_payload_mixed_finding(
                        source_statute=amendment_id,
                        target_chapter=chapter_label,
                        adopted_count=adopted_count,
                        owned_count=owned_count,
                    )
                )

    return result


def _apply_uncovered_kumotaan(
    state: "ReplayState",
    ctx: "StatuteContext",
    ops: List[AmendmentOp],
    johto: str,
    amendment_id: str,
    lo_ops_out: Optional[List[_LegalOperation]] = None,
    op_source: Optional[OperationSource] = None,
    findings_out: Optional[List[Finding]] = None,
) -> "ReplayState":
    """Apply uncovered repeals from kumotaan clauses."""
    def _same_amendment_non_repeal_section_labels() -> Set[str]:
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
            return state

    has_peg_repeals = any(op.op_type == "REPEAL" for op in ops)
    has_vts_repeals = bool(vts_section_refs or vts_container_refs["chapter"] or vts_container_refs["part"])
    if not has_peg_repeals and not has_vts_repeals and not re.search(r"\bkumotaan\b", johto, re.IGNORECASE):
        return state

    covered_labels: Set[str] = set()
    covered_containers: Set[tuple[str, str]] = set()
    for op in ops:
        if op.voimaantulo_repeal:
            continue
        if op.target_unit_kind == "section" and op.target_section:
            covered_labels.add(_norm_num_token(op.target_section))
        elif op.target_unit_kind in {"chapter", "part"} and op.target_section:
            covered_containers.add((op.target_unit_kind, _norm_num_token(op.target_section)))
    covered_labels |= _same_amendment_non_repeal_section_labels()

    kumotaan_refs = _extract_kumotaan_section_refs(johto)
    for label in vts_section_refs:
        if label and label not in kumotaan_refs:
            kumotaan_refs.append(label)
    kumotaan_containers = _extract_kumotaan_container_refs(johto)
    for kind_name, labels in vts_container_refs.items():
        if labels:
            kumotaan_containers.setdefault(kind_name, [])
            for label in labels:
                if label and label not in kumotaan_containers[kind_name]:
                    kumotaan_containers[kind_name].append(label)

    repealed: List[str] = []
    seen_recovery_findings: Set[tuple[str, str, str, str]] = set()

    def _append_recovery_finding(
        *,
        op_id: str,
        target_unit_kind: str,
        target_norm: str,
        target_chapter: str | None = None,
    ) -> None:
        if findings_out is None:
            return
        finding = _uncovered_body_recovery_finding(
            op_id=op_id,
            source_statute=amendment_id,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
        )
        if finding is None:
            return
        key = (
            str(finding.kind or ""),
            str(target_unit_kind or ""),
            str(target_norm or ""),
            str(target_chapter or ""),
        )
        if key in seen_recovery_findings:
            return
        seen_recovery_findings.add(key)
        findings_out.append(finding)

    for ref in kumotaan_refs:
        label = _norm_num_token(ref)
        if not label or label in covered_labels:
            continue
        if label in vts_granular_section_refs and label not in vts_section_refs:
            continue
        covered_labels.add(label)

        sec_path = state.find_section_path(label)
        if sec_path is None:
            continue

        sec_node = _tops.resolve(state.ir, sec_path)
        assert sec_node is not None, f"resolve failed for {sec_path}"
        _base_path = _tops.find(ctx.base_ir, "section", label)
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
                )
            )
        _append_recovery_finding(
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
            existing_path = state.find(node_kind, label)
            if not label:
                continue
            if (target_unit_kind, label) in covered_containers and existing_path is None:
                continue
            covered_containers.add((target_unit_kind, label))

            if existing_path is None:
                continue

            state = state.with_ir(_tops.remove_at(state.ir, existing_path))
            repealed_containers.append(f"{label} {kind_name}")

            op_id = f"uncovered_repeal_{target_unit_kind}_{label}"
            if lo_ops_out is not None:
                tl_path = tuple((k, v) for k, v in existing_path if v)
                lo_ops_out.append(
                        _LegalOperation(
                            op_id=op_id,
                            sequence=0,
                            action=StructuralAction.REPEAL,
                        target=LegalAddress(path=tl_path),
                        payload=None,
                        source=op_source,
                        group_id=f"finland-johto:{amendment_id}",
                    )
                )
            _append_recovery_finding(
                op_id=op_id,
                target_unit_kind=target_unit_kind,
                target_norm=label,
            )

    if repealed:
        _replay_print(f"  [{amendment_id}] uncovered kumotaan: {repealed}")
    if repealed_containers:
        _replay_print(f"  [{amendment_id}] uncovered kumotaan containers: {repealed_containers}")
    return state


def _pre_scan_repeal_targets(
    muutoslait: List[str],
    corpus_store: "CorpusStore",
    parent_id: str = "",
    parent_title: str = "",
    cutoff_date: Optional[dt.date] = None,
    vts_skipped_targets_out: Optional[List[VtsSkippedTarget]] = None,
    vts_source_diagnostics_out: Optional[List[VtsSourceDiagnostic]] = None,
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
    from lawvm.finland.grafter import RepealTargetRef

    per_amendment: List[Set[RepealTargetRef]] = []

    for amendment_id in muutoslait:
        targets: Set[RepealTargetRef] = set()
        xml_bytes = corpus_store.read_source(amendment_id)
        if xml_bytes is None:
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
                except (ValueError, KeyError, AttributeError, TypeError, IndexError):
                    pass  # vts extraction is best-effort in pre-scan
        except (ValueError, KeyError, AttributeError, TypeError, IndexError, etree.XMLSyntaxError):
            pass  # pre-scan errors are non-fatal — just emit empty set
        per_amendment.append(targets)

    return per_amendment

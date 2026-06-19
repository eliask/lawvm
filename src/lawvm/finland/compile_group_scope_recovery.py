"""Pre-snapshot scope and action-family recovery for Finland group lowering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import Optional, Sequence

from lawvm.core.compile_result import StrictProfile
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.lowering_scope_recovery import (
    allow_unscoped_live_section_retarget,
    group_has_scope_source,
)
from lawvm.finland.ops import (
    AmendmentOp,
    FailedOp,
    ScopeConfidence,
    normalize_scope_confidence,
    projection_scope_confidence,
    _lo_with_path_update,
)
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState


_REJECTED_OPERATION_MESSAGE = "operation rejected before apply"
_STAGE = "_compile_group"
_BODY_CHAPTER_INSERT_SCOPE_RULE_ID = "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
_BODY_CHAPTER_MOVE_RULE_ID = "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE"
_LIVE_SECTION_RETARGET_RULE_ID = "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"


@dataclass(frozen=True, slots=True)
class CompileGroupScopeRecoveryRequest:
    """Inputs for pre-snapshot scope/action recovery."""

    master: ReplayState
    target_unit_kind: TargetUnitKind
    target_norm: str
    target_chapter: Optional[str]
    target_part: Optional[str]
    group_ops: list[AmendmentOp]
    inserted_chapter_labels: set[str]
    source_model: AmendmentSourceModel
    strict_profile: Optional[StrictProfile]


@dataclass(frozen=True, slots=True)
class CompileGroupScopeRecoveryResult:
    """Recovered scope/action state for later compile-group phases."""

    effective_target_chapter: Optional[str]
    effective_target_part: Optional[str]
    surface_target_chapter: Optional[str]
    surface_target_part: Optional[str]
    group_ops: list[AmendmentOp]
    blocked: bool = False


def _rejected_operation_findings(failed_ops: list[FailedOp]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for failed in failed_ops:
        findings.append(
            Finding(
                kind="ELAB.REJECTED_OPERATION",
                role="observation",
                stage=_STAGE,
                detail={**failed.as_detail(), "message": _REJECTED_OPERATION_MESSAGE},
                source_statute=failed.amendment_id,
                blocking=False,
            )
        )
    for failed in failed_ops:
        findings.append(
            Finding(
                kind="ELAB.STRICT_REJECTED_OPERATION",
                role="obligation",
                stage=_STAGE,
                detail={**failed.as_detail(), "message": _REJECTED_OPERATION_MESSAGE},
                source_statute=failed.amendment_id,
                blocking=True,
            )
        )
    return tuple(findings)


def _source_statute(group_ops: list[AmendmentOp]) -> str:
    return next((str(op.source_statute or "") for op in group_ops if op.source_statute), "")


def _group_has_explicit_chapter_scope(group_ops: list[AmendmentOp]) -> bool:
    for op in group_ops:
        witness = projection_scope_confidence(
            scope_confidence=op.scope_confidence,
            scope_provenance_tags=op.scope_provenance_tags,
            resolved_chapter=op.target_chapter,
        )
        if witness is not None and witness.is_explicit and witness.resolved_chapter:
            return True
    return False


def _group_has_scope_that_overrides_body_wrapper(
    group_ops: list[AmendmentOp],
    *,
    target_chapter: str,
) -> bool:
    """Return True when earlier scope recovery owns a non-body target chapter."""
    for op in group_ops:
        witness = projection_scope_confidence(
            scope_confidence=op.scope_confidence,
            scope_provenance_tags=op.scope_provenance_tags,
            resolved_chapter=op.target_chapter,
        )
        if (
            witness is not None
            and witness.resolved_chapter == target_chapter
            and witness.source in {"live_stem_host", "explicit_scope_rewrite"}
        ):
            return True
    return False


def _source_body_is_single_mixed_chapter_wrapper(
    source_model: AmendmentSourceModel,
    body_chapter: str,
    master: ReplayState,
) -> bool:
    body_chapter_norm = _norm_num_token(body_chapter)
    real_chapter_labels = {
        _norm_num_token(unit.label)
        for unit in source_model.observed_body_inventory()
        if unit.kind == "chapter" and unit.source_tag == "chapter"
    }
    if real_chapter_labels != {body_chapter_norm}:
        return False

    foreign_live_chapters: set[str] = set()
    for unit in source_model.observed_body_inventory():
        if unit.kind != "section" or _norm_num_token(unit.chapter_label) != body_chapter_norm:
            continue
        section_label = _norm_num_token(unit.label)
        section_path = master.find_section_path(section_label, None, unit.part_label or None)
        if section_path is None:
            stem_match = re.fullmatch(r"(\d+)[a-z]+", section_label, re.I)
            if stem_match is not None:
                section_path = master.find_section_path(
                    stem_match.group(1),
                    None,
                    unit.part_label or None,
                )
        if section_path is None:
            continue
        live_chapter = next((label for kind, label in section_path if kind == "chapter"), "")
        if live_chapter and _norm_num_token(live_chapter) != body_chapter_norm:
            foreign_live_chapters.add(_norm_num_token(live_chapter))
    return len(foreign_live_chapters) >= 2


def _live_chapter_has_no_section_children(master: ReplayState, chapter_label: str) -> bool:
    chapter = master.find_chapter(chapter_label)
    if chapter is None:
        return False
    return not any(child.kind is IRNodeKind.SECTION for child in chapter.children)


def _body_chapter_corrected_ops(
    group_ops: list[AmendmentOp],
    *,
    target_norm: str,
    target_chapter: Optional[str],
    resolved_body_chapter: str,
) -> list[AmendmentOp]:
    return [
        dc_replace(
            op,
            target_chapter=resolved_body_chapter,
            scope_confidence=normalize_scope_confidence(
                projection_scope_confidence(
                    scope_confidence=op.scope_confidence,
                    scope_provenance_tags=op.scope_provenance_tags,
                    resolved_chapter=resolved_body_chapter,
                ),
                resolved_chapter=resolved_body_chapter,
            ),
            lo=_lo_with_path_update(op.lo, chapter=resolved_body_chapter) if op.lo is not None else op.lo,
        )
        if (
            op.target_unit_kind == "section"
            and _norm_num_token(op.target_section or "") == target_norm
            and op.target_chapter == target_chapter
        )
        else op
        for op in group_ops
    ]


def _replace_to_insert_ops(
    group_ops: list[AmendmentOp],
    *,
    target_norm: str,
    target_chapter: str,
    replacement_chapter: str,
) -> list[AmendmentOp]:
    rewritten: list[AmendmentOp] = []
    for op in group_ops:
        if not (
            op.target_unit_kind == "section"
            and _norm_num_token(op.target_section or "") == target_norm
            and op.target_chapter == target_chapter
            and op.op_type == "REPLACE"
        ):
            rewritten.append(op)
            continue
        rewritten.append(
            dc_replace(
                op,
                op_type="INSERT",
                target_chapter=replacement_chapter,
                body_chapter_move_from=target_chapter,
                target_special=None,
                scope_confidence=normalize_scope_confidence(
                    projection_scope_confidence(
                        scope_confidence=op.scope_confidence,
                        scope_provenance_tags=op.scope_provenance_tags,
                        resolved_chapter=replacement_chapter,
                    ),
                    resolved_chapter=replacement_chapter,
                ),
                lo=(
                    dc_replace(
                        (_tmp_lo := _lo_with_path_update(op.lo, chapter=replacement_chapter)),
                        action=StructuralAction.INSERT,
                        target=dc_replace(_tmp_lo.target, special=None),
                    )
                    if op.lo is not None
                    else op.lo
                ),
            )
        )
    return rewritten


def _retargeted_live_section_ops(
    group_ops: list[AmendmentOp],
    *,
    target_norm: str,
    target_chapter: Optional[str],
    live_chapter: str,
    live_part: Optional[str],
    stale_part: Optional[str],
    retarget_scope_source: str,
) -> list[AmendmentOp]:
    rewritten: list[AmendmentOp] = []
    for op in group_ops:
        if not (
            op.target_unit_kind == "section"
            and _norm_num_token(op.target_section or "") == target_norm
            and op.target_chapter == target_chapter
        ):
            rewritten.append(op)
            continue
        rewritten.append(
            dc_replace(
                op,
                target_part=live_part,
                target_chapter=live_chapter,
                scope_confidence=(
                    ScopeConfidence(
                        tag="body_container_membership_rewrite",
                        source="explicit_scope_rewrite",
                        confidence="rewritten",
                        resolved_chapter=live_chapter,
                    )
                    if retarget_scope_source == "explicit_chunk"
                    else normalize_scope_confidence(
                        projection_scope_confidence(
                            scope_confidence=op.scope_confidence,
                            scope_provenance_tags=op.scope_provenance_tags,
                            resolved_chapter=live_chapter,
                        ),
                        resolved_chapter=live_chapter,
                    )
                ),
                lo=(
                    dc_replace(
                        _lo_with_path_update(op.lo, part=live_part, chapter=live_chapter),
                        provenance_tags=tuple(
                            _lo_with_path_update(
                                op.lo,
                                part=live_part,
                                chapter=live_chapter,
                            ).provenance_tags
                        )
                        + tuple(
                            tag
                            for tag in (
                                f"body_part_retargeted_from:{stale_part}" if stale_part else "",
                                f"body_chapter_retargeted_from:{target_chapter}" if target_chapter else "",
                            )
                            if tag
                        ),
                    )
                    if op.lo is not None
                    else op.lo
                ),
            )
        )
    return rewritten


def _maybe_apply_body_chapter_insert_correction(
    request: CompileGroupScopeRecoveryRequest,
    result: CompileGroupScopeRecoveryResult,
) -> PhaseResult[CompileGroupScopeRecoveryResult]:
    if request.target_unit_kind != "section":
        return PhaseResult(output=result)
    body_chapter = request.source_model.body_section_chapter(request.target_norm)
    resolved_body_chapter = body_chapter
    carry_forward_scoped = group_has_scope_source(request.group_ops, "carry_forward")
    explicit_chapter_scoped = _group_has_explicit_chapter_scope(request.group_ops)
    body_chapter_is_subchapter = (
        body_chapter is not None
        and request.target_chapter is not None
        and re.fullmatch(rf"{re.escape(request.target_chapter)}[a-z]+", body_chapter, re.I)
        is not None
    )
    body_chapter_is_letter_suffix = (
        body_chapter is not None
        and re.fullmatch(r"\d+[a-z]+", body_chapter, re.I) is not None
    )
    inserted_chapter_labels = {
        _norm_num_token(label) for label in request.inserted_chapter_labels
    }
    body_chapter_is_inserted = (
        body_chapter is not None
        and _norm_num_token(body_chapter) in inserted_chapter_labels
    )
    body_chapter_is_empty_live_chapter = (
        body_chapter is not None
        and _live_chapter_has_no_section_children(request.master, body_chapter)
    )
    body_wrapper_overridden_by_scope = (
        body_chapter is not None
        and request.target_chapter is not None
        and body_chapter != request.target_chapter
        and _source_body_is_single_mixed_chapter_wrapper(
            request.source_model,
            body_chapter,
            request.master,
        )
        and not body_chapter_is_inserted
        and _group_has_scope_that_overrides_body_wrapper(
            request.group_ops,
            target_chapter=request.target_chapter,
        )
    )
    group_targets_whole_section = any(
        op.target_unit_kind == "section"
        and _norm_num_token(op.target_section or "") == request.target_norm
        and op.target_chapter == request.target_chapter
        and not op.target_paragraph
        and not op.target_item
        and not op.target_special
        for op in request.group_ops
    )
    source_owned_inserted_chapter_scope = (
        body_chapter is not None
        and request.target_chapter is not None
        and (
            body_chapter_is_subchapter
            or (
                (body_chapter_is_inserted or body_chapter_is_empty_live_chapter)
                and not explicit_chapter_scoped
                and not carry_forward_scoped
            )
            or (
                carry_forward_scoped
                and not body_chapter_is_letter_suffix
                and body_chapter_is_inserted
            )
        )
        and request.source_model.body_has_real_chapter_container(body_chapter)
        and not body_wrapper_overridden_by_scope
    )
    if body_chapter is not None and (
        group_targets_whole_section
        and (
            not request.target_chapter
            or body_chapter_is_subchapter
            or (not explicit_chapter_scoped and body_chapter == request.target_chapter)
        )
    ):
        sibling_consensus_scope = request.source_model.retarget_duplicate_body_section_scope_from_close_live_siblings(
            section_norm=request.target_norm,
            body_chapter=body_chapter,
            body_part=request.target_part,
            master=request.master,
        )
        if sibling_consensus_scope is not None:
            _sibling_part, sibling_chapter = sibling_consensus_scope
            if sibling_chapter != body_chapter:
                resolved_body_chapter = sibling_chapter
    if (
        body_chapter is not None
        and all(str(op.target_special or "").strip() == "otsikko" for op in result.group_ops)
    ):
        resolved_body_chapter = request.source_model.retarget_heading_insert_body_chapter_from_close_live_sibling(
            section_norm=request.target_norm,
            body_chapter=body_chapter,
            master=request.master,
        )
    if resolved_body_chapter is None or resolved_body_chapter == (request.target_chapter or ""):
        return PhaseResult(output=result)
    apply_correction = False
    if (
        body_chapter is not None
        and resolved_body_chapter != body_chapter
        and request.master.find("chapter", resolved_body_chapter) is not None
    ):
        apply_correction = True
    elif not request.target_chapter:
        apply_correction = request.master.find("chapter", resolved_body_chapter) is not None
    elif source_owned_inserted_chapter_scope:
        apply_correction = True
    elif carry_forward_scoped:
        apply_correction = (
            re.fullmatch(rf"{re.escape(request.target_chapter)}[a-z]", resolved_body_chapter, re.I)
            is not None
        )
    if not apply_correction:
        return PhaseResult(output=result)
    corrected = dc_replace(
        result,
        effective_target_chapter=resolved_body_chapter,
        group_ops=_body_chapter_corrected_ops(
            result.group_ops,
            target_norm=request.target_norm,
            target_chapter=request.target_chapter,
            resolved_body_chapter=resolved_body_chapter,
        ),
    )
    finding = Finding(
        kind=_BODY_CHAPTER_INSERT_SCOPE_RULE_ID,
        role="observation",
        stage=_STAGE,
        detail={
            "rule_id": _BODY_CHAPTER_INSERT_SCOPE_RULE_ID,
            "phase": "lowering",
            "family": "target_resolution_recovery",
            "reason": "amendment_body_chapter_corrects_insert_target_scope",
            "target_unit_kind": request.target_unit_kind,
            "target_norm": request.target_norm,
            "target_chapter": request.target_chapter or "",
            "target_part": request.target_part or "",
            "body_chapter": body_chapter or "",
            "resolved_body_chapter": resolved_body_chapter,
            "blocking": True,
            "strict_disposition": "record",
            "quirks_disposition": "apply",
        },
        source_statute=_source_statute(result.group_ops),
        blocking=False,
    )
    return PhaseResult(output=corrected, findings=(finding,))


def _replacement_ops(
    group_ops: list[AmendmentOp],
    *,
    target_norm: str,
    target_chapter: Optional[str],
) -> list[AmendmentOp]:
    return [
        op
        for op in group_ops
        if (
            op.target_unit_kind == "section"
            and _norm_num_token(op.target_section or "") == target_norm
            and op.target_chapter == target_chapter
            and op.op_type == "REPLACE"
            and not op.target_paragraph
            and not op.target_item
            and not op.target_special
        )
    ]


def _maybe_apply_replace_to_insert_move(
    request: CompileGroupScopeRecoveryRequest,
    result: CompileGroupScopeRecoveryResult,
) -> PhaseResult[CompileGroupScopeRecoveryResult]:
    if (
        request.target_unit_kind != "section"
        or not request.target_chapter
        or not any(op.op_type == "REPLACE" for op in request.group_ops)
    ):
        return PhaseResult(output=result)
    body_chapter = request.source_model.body_section_chapter(request.target_norm)
    trigger_evidence = tuple(
        evidence
        for evidence, present in (
            (
                "pseudo_chapter_marker",
                body_chapter is not None
                and request.source_model.body_has_pseudo_chapter_marker(body_chapter),
            ),
            (
                "real_inserted_chapter",
                body_chapter is not None
                and request.master.find("chapter", body_chapter) is None
                and request.source_model.body_has_real_chapter_container(body_chapter),
            ),
            (
                "inserted_chapter_op",
                body_chapter is not None and body_chapter in request.inserted_chapter_labels,
            ),
        )
        if present
    )
    if not (
        body_chapter is not None
        and body_chapter != request.target_chapter
        and re.fullmatch(rf"{re.escape(request.target_chapter)}[a-z]+", body_chapter, re.I)
        is not None
        and trigger_evidence
    ):
        return PhaseResult(output=result)
    replacement_ops = _replacement_ops(
        result.group_ops,
        target_norm=request.target_norm,
        target_chapter=request.target_chapter,
    )
    if not replacement_ops:
        return PhaseResult(output=result)
    finding = Finding(
        kind=_BODY_CHAPTER_MOVE_RULE_ID,
        role="observation",
        stage=_STAGE,
        detail={
            "rule_id": _BODY_CHAPTER_MOVE_RULE_ID,
            "phase": "lowering",
            "family": "action_family_recovery",
            "reason": "body_chapter_suffix_restructure_requires_move_bridge",
            "original_action": "REPLACE",
            "lowered_action": "INSERT",
            "target_unit_kind": request.target_unit_kind,
            "target_norm": request.target_norm,
            "target_chapter": request.target_chapter,
            "target_part": request.target_part or "",
            "body_chapter": body_chapter,
            "trigger_evidence": trigger_evidence,
            "op_ids": tuple(str(op.op_id or "") for op in replacement_ops),
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
        source_statute=_source_statute(replacement_ops),
        blocking=False,
    )
    if (
        request.strict_profile is not None
        and not request.strict_profile.allows_context_dependent_anchor_resolution
    ):
        failed_ops = [
            FailedOp.from_scope(
                amendment_id=str(op.source_statute or ""),
                description=op.description(),
                reason=(
                    "section REPLACE was lowered to INSERT+MOVE because the amendment body "
                    "placed the section under a new letter-suffix chapter"
                ),
                reason_code=_BODY_CHAPTER_MOVE_RULE_ID,
                target_section=op.target_section or request.target_norm,
                target_unit_kind=op.target_unit_kind,
                target_chapter=request.target_chapter,
                target_part=request.target_part,
            )
            for op in replacement_ops
        ]
        return PhaseResult(
            output=dc_replace(result, blocked=True),
            findings=(finding, *_rejected_operation_findings(failed_ops)),
        )
    recovered = dc_replace(
        result,
        effective_target_chapter=body_chapter,
        surface_target_chapter=body_chapter,
        group_ops=_replace_to_insert_ops(
            result.group_ops,
            target_norm=request.target_norm,
            target_chapter=request.target_chapter,
            replacement_chapter=body_chapter,
        ),
    )
    return PhaseResult(output=recovered, findings=(finding,))


def _maybe_retarget_live_section(
    request: CompileGroupScopeRecoveryRequest,
    result: CompileGroupScopeRecoveryResult,
) -> PhaseResult[CompileGroupScopeRecoveryResult]:
    all_whole_section_inserts = all(
        op.op_type == "INSERT"
        and op.target_unit_kind == "section"
        and not op.target_paragraph
        and not op.target_item
        and not op.target_special
        for op in request.group_ops
    )
    if (
        request.target_unit_kind != "section"
        or not (request.target_chapter or request.target_part)
        or all_whole_section_inserts
    ):
        return PhaseResult(output=result)
    scoped_path = request.master.find_section_path(
        request.target_norm,
        request.target_chapter,
        request.target_part,
    )
    authorized_retarget_scope_source = (
        allow_unscoped_live_section_retarget(request.group_ops)
        if scoped_path is None and request.target_norm not in request.master.duplicate_section_labels
        else None
    )
    if scoped_path is None and request.target_norm not in request.master.duplicate_section_labels:
        if request.target_chapter:
            source_body_chapter = request.source_model.source_body_chapter_for_scoped_section_target(
                target_norm=request.target_norm,
                target_chapter=request.target_chapter,
                target_part=request.target_part,
            )
            if (
                source_body_chapter == request.target_chapter
                and authorized_retarget_scope_source is None
            ):
                scoped_path = ()
    retarget_scope_source = (
        authorized_retarget_scope_source
        if scoped_path is None and request.target_norm not in request.master.duplicate_section_labels
        else None
    )
    sibling_consensus_live_scope: tuple[str | None, str] | None = None
    group_targets_whole_section = any(
        op.target_unit_kind == "section"
        and _norm_num_token(op.target_section or "") == request.target_norm
        and op.target_chapter == request.target_chapter
        and not op.target_paragraph
        and not op.target_item
        and not op.target_special
        for op in request.group_ops
    )
    if (
        scoped_path is None
        and request.target_norm in request.master.duplicate_section_labels
        and group_targets_whole_section
    ):
        body_scope = request.source_model.source_body_scope_for_section_target(request.target_norm)
        if body_scope is not None:
            body_part, body_chapter = body_scope
            sibling_consensus_live_scope = request.source_model.retarget_duplicate_body_section_scope_from_close_live_siblings(
                section_norm=request.target_norm,
                body_chapter=body_chapter or "",
                body_part=body_part,
                master=request.master,
            )
            if sibling_consensus_live_scope is not None:
                retarget_scope_source = "close_live_sibling_consensus"
    if retarget_scope_source is None:
        return PhaseResult(output=result)
    body_scope = request.source_model.source_body_scope_for_section_target(request.target_norm)
    body_part = None
    body_chapter = None
    live_path = None
    if body_scope is not None:
        body_part, body_chapter = body_scope
        if sibling_consensus_live_scope is not None:
            live_part_hint, live_chapter_hint = sibling_consensus_live_scope
            live_path = request.master.find_section_path(
                request.target_norm,
                live_chapter_hint,
                live_part_hint,
            )
        else:
            live_path = request.master.find_section_path(request.target_norm, body_chapter, body_part)
    if live_path is None and sibling_consensus_live_scope is None:
        live_path = request.master.find_section_path(request.target_norm, None, request.target_part)
    if (
        live_path is None
        and sibling_consensus_live_scope is None
        and retarget_scope_source == "explicit_chunk"
    ):
        live_path = request.master.find_section_path(request.target_norm, None, None)
    if live_path is None:
        return PhaseResult(output=result)
    live_part = next((label for kind, label in live_path if kind == "part"), None)
    live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
    if not live_chapter or (
        live_chapter == request.target_chapter and live_part == request.target_part
    ):
        return PhaseResult(output=result)
    retarget_detail = {
        "target_unit_kind": request.target_unit_kind,
        "target_norm": request.target_norm,
        "target_chapter": request.target_chapter or "",
        "target_part": request.target_part or "",
        "body_part": body_part or "",
        "body_chapter": body_chapter or "",
        "resolved_live_part": live_part or "",
        "resolved_live_chapter": live_chapter,
        "scope_source": retarget_scope_source,
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }
    finding = Finding(
        kind=_LIVE_SECTION_RETARGET_RULE_ID,
        role="observation",
        stage=_STAGE,
        detail=retarget_detail,
        source_statute=_source_statute(result.group_ops),
        blocking=False,
    )
    if (
        request.strict_profile is not None
        and not request.strict_profile.allows_context_dependent_anchor_resolution
    ):
        failed_ops = [
            FailedOp.from_scope(
                amendment_id=str(op.source_statute or ""),
                description=op.description(),
                reason=(
                    "scoped section target rebounded to a body-backed unique live "
                    "section path outside explicit source scope"
                ),
                reason_code=_LIVE_SECTION_RETARGET_RULE_ID,
                target_section=op.target_section or request.target_norm,
                target_unit_kind=op.target_unit_kind,
                target_chapter=request.target_chapter,
            )
            for op in result.group_ops
            if (
                op.target_unit_kind == "section"
                and _norm_num_token(op.target_section or "") == request.target_norm
                and op.target_chapter == request.target_chapter
            )
        ]
        return PhaseResult(
            output=dc_replace(result, blocked=True),
            findings=(finding, *_rejected_operation_findings(failed_ops)),
        )
    recovered = dc_replace(
        result,
        effective_target_chapter=live_chapter,
        effective_target_part=live_part,
        surface_target_part=body_part if body_scope is not None else result.surface_target_part,
        surface_target_chapter=body_chapter if body_scope is not None else result.surface_target_chapter,
        group_ops=_retargeted_live_section_ops(
            result.group_ops,
            target_norm=request.target_norm,
            target_chapter=request.target_chapter,
            live_chapter=live_chapter,
            live_part=live_part,
            stale_part=request.target_part,
            retarget_scope_source=retarget_scope_source,
        ),
    )
    return PhaseResult(output=recovered, findings=(finding,))


def _body_chapter_insert_correction_candidate(group_ops: Sequence[AmendmentOp]) -> bool:
    return all(
        op.op_type == "INSERT"
        or (op.op_type == "REPLACE" and str(op.target_special or "").strip() == "otsikko")
        for op in group_ops
    )


def resolve_compile_group_scope_recovery(
    request: CompileGroupScopeRecoveryRequest,
) -> PhaseResult[CompileGroupScopeRecoveryResult]:
    """Resolve pre-snapshot scope/action recovery for one compile group."""
    surface_target_chapter, surface_target_part = request.source_model.resolve_group_surface_scope(
        target_unit_kind=request.target_unit_kind,
        target_norm=request.target_norm,
        target_chapter=request.target_chapter,
        target_part=request.target_part,
        group_ops=request.group_ops,
    )
    result = CompileGroupScopeRecoveryResult(
        effective_target_chapter=request.target_chapter,
        effective_target_part=request.target_part,
        surface_target_chapter=surface_target_chapter,
        surface_target_part=surface_target_part,
        group_ops=request.group_ops,
    )
    findings: tuple[Finding, ...] = ()
    if _body_chapter_insert_correction_candidate(request.group_ops):
        insert_result = _maybe_apply_body_chapter_insert_correction(request, result)
        result = insert_result.output
        findings += insert_result.findings()
    replace_result = _maybe_apply_replace_to_insert_move(request, result)
    result = replace_result.output
    findings += replace_result.findings()
    if result.blocked:
        return PhaseResult(output=result, findings=findings)
    retarget_result = _maybe_retarget_live_section(request, result)
    result = retarget_result.output
    findings += retarget_result.findings()
    return PhaseResult(output=result, findings=findings)

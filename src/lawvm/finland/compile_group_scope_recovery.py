"""Pre-snapshot scope and action-family recovery for Finland group lowering."""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import Optional, Sequence

from lawvm.core import tree_ops as _tops
from lawvm.core.compile_result import StrictProfile
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johto_scope_mentions import collect_johto_chapter_scope_mentions
from lawvm.finland.lowering_scope_recovery import (
    allow_unscoped_live_section_retarget,
    group_has_scope_source,
)
from lawvm.finland.ops import (
    AmendmentOp,
    FailedOp,
    ScopeConfidence,
    ScopeResolutionConfidence,
    ScopeResolutionSource,
    normalize_scope_confidence,
    projection_scope_confidence,
    _lo_with_path_update,
    _op_target_subsection_label,
)
from lawvm.finland.scope import _johtolause_explicitly_binds_chapter_section
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.statute import ReplayState


_REJECTED_OPERATION_MESSAGE = "operation rejected before apply"
_STAGE = "_compile_group"
_BODY_CHAPTER_INSERT_SCOPE_RULE_ID = "LOWER.BODY_CHAPTER_INSERT_SCOPE_CORRECTION"
_BODY_CHAPTER_MOVE_RULE_ID = "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE"
_BODY_CHAPTER_DECLARED_MOVE_RULE_ID = "LOWER.BODY_CHAPTER_DECLARED_MOVE_REPLACE"
_ITEM_AS_SUBSECTION_TARGET_REWRITE_RULE_ID = "LOWER.ITEM_AS_SUBSECTION_TARGET_REWRITE"
_LIVE_SECTION_RETARGET_RULE_ID = "LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET"
_FI_LABEL_TOKEN = r"\d{1,4}\s{0,3}[a-z]?"
_FI_RANGE_DASH = r"[\-\u2010-\u2015]"
_COMBINED_ROOT_INSERT_CHAPTER_SECTION_RANGE_RE = re.compile(
    rf"\buusi\s+(?P<chapter_start>{_FI_LABEL_TOKEN})\s*{_FI_RANGE_DASH}\s*"
    rf"(?P<chapter_end>{_FI_LABEL_TOKEN})\s+luku\s+ja\s+"
    rf"(?P<section_start>{_FI_LABEL_TOKEN})\s*{_FI_RANGE_DASH}\s*"
    rf"(?P<section_end>{_FI_LABEL_TOKEN})\s*§",
    re.IGNORECASE,
)


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
    johto: str = ""
    amendment_group_ops: tuple[AmendmentOp, ...] = ()


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


def _group_has_witness_rule(group_ops: list[AmendmentOp], rule_id: str) -> bool:
    return any(op.witness_rule_id == rule_id for op in group_ops)


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


def _group_has_live_scoped_target_path(
    master: ReplayState,
    group_ops: list[AmendmentOp],
    *,
    target_norm: str,
    target_chapter: str,
    target_part: Optional[str],
) -> bool:
    if master.find_section_path(target_norm, target_chapter, target_part) is None:
        return False
    matching_ops = [
        op
        for op in group_ops
        if op.target_unit_kind == "section"
        and _norm_num_token(op.target_section or "") == target_norm
    ]
    if not matching_ops:
        return False
    for op in matching_ops:
        if op.target_chapter != target_chapter:
            return False
        if op.lo is None:
            return False
        lo_chapter = next((label for kind, label in op.lo.target.path if kind == "chapter"), None)
        if lo_chapter != target_chapter:
            return False
    return True


def _source_body_is_single_mixed_chapter_wrapper(
    source_model: AmendmentSourceModel,
    body_chapter: str,
    master: ReplayState,
) -> bool:
    return source_model.body_chapter_is_single_mixed_wrapper(body_chapter, master)


def _label_in_closed_range(label: str, start: str, end: str) -> bool:
    label_key = _tops.default_label_sort_key(_norm_num_token(label))
    start_key = _tops.default_label_sort_key(_norm_num_token(start))
    end_key = _tops.default_label_sort_key(_norm_num_token(end))
    if end_key < start_key:
        start_key, end_key = end_key, start_key
    return start_key <= label_key <= end_key


def _combined_root_insert_range_owns_section(
    source_model: AmendmentSourceModel,
    *,
    body_chapter: str,
    target_norm: str,
) -> bool:
    """Return True for formulas like ``uusi 5a-5c luku ja 20a-20h §``.

    In that family the trailing section range belongs to the newly inserted
    chapter range's body wrapper.  A live stem-host guess is weaker evidence
    than this explicit combined source range.
    """
    preamble = source_model.preamble_text()
    if "luku" not in preamble or "§" not in preamble:
        return False
    # lawvm-regex: owning_parser recognizes the combined chapter-range + section-range source formula to decide range-ownership; produces a typed scope verdict + Finding, mints no op
    for match in _COMBINED_ROOT_INSERT_CHAPTER_SECTION_RANGE_RE.finditer(preamble):
        if not _label_in_closed_range(
            body_chapter,
            match.group("chapter_start"),
            match.group("chapter_end"),
        ):
            continue
        if _label_in_closed_range(
            target_norm,
            match.group("section_start"),
            match.group("section_end"),
        ):
            return True
    return False


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
    body_chapter_move_from: Optional[str] = None,
) -> list[AmendmentOp]:
    return [
        dc_replace(
            op,
            target_chapter=resolved_body_chapter,
            body_chapter_move_from=body_chapter_move_from or op.body_chapter_move_from,
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


def _replace_to_declared_move_ops(
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
        rewritten_lo = None
        if op.lo is not None:
            moved_lo = _lo_with_path_update(op.lo, chapter=replacement_chapter)
            rewritten_lo = dc_replace(
                moved_lo,
                action=StructuralAction.REPLACE,
                target=dc_replace(moved_lo.target, special=None),
                move_clause_target_unit_kind="chapter",
                provenance_tags=tuple(moved_lo.provenance_tags)
                + ("body_chapter_declared_move_replace",),
            )
        rewritten.append(
            dc_replace(
                op,
                target_chapter=replacement_chapter,
                move_clause_target_unit_kind="chapter",
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
                lo=rewritten_lo,
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
                        source=ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
                        confidence=ScopeResolutionConfidence.REWRITTEN,
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


def _section_has_subsection_label(section: object, label: str) -> bool:
    wanted = _norm_num_token(label)
    return any(
        child.kind is IRNodeKind.SUBSECTION
        and child.label is not None
        and _norm_num_token(child.label) == wanted
        for child in getattr(section, "children", ())
    )


def _section_subsection_has_paragraph_children(
    section: object,
    subsection_label: int | str | None,
) -> bool:
    if subsection_label is None:
        return False
    wanted = _norm_num_token(str(subsection_label))
    for child in getattr(section, "children", ()):
        if (
            child.kind is IRNodeKind.SUBSECTION
            and child.label is not None
            and _norm_num_token(child.label) == wanted
        ):
            return any(grandchild.kind is IRNodeKind.PARAGRAPH for grandchild in child.children)
    return False


def _section_is_definition_entry_list(section: object) -> bool:
    """Return True for FI sections whose numbered children are definition entries."""
    heading_text = " ".join(
        str(getattr(child, "text", "") or "")
        for child in getattr(section, "children", ())
        if child.kind is IRNodeKind.HEADING
    ).casefold()
    return "määritel" in heading_text


def _source_payload_has_subsection_label(
    source_model: AmendmentSourceModel,
    *,
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
    subsection_label: str,
) -> bool:
    lookups = (
        source_model.lookup_payload_ir(
            "section",
            target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
        ),
        source_model.lookup_payload_ir("section", target_norm),
    )
    wanted = _norm_num_token(subsection_label)

    def _node_text(node: object) -> str:
        pieces: list[str] = []

        def walk(current: object) -> None:
            text = getattr(current, "text", None)
            if text:
                pieces.append(str(text))
            for child in getattr(current, "children", ()):
                walk(child)

        walk(node)
        return " ".join(" ".join(pieces).split())

    def _text_starts_with_entry_label(text: str) -> bool:
        normalized_text = " ".join(str(text or "").strip().lower().split())
        if not normalized_text:
            return False
        variants = {wanted}
        if len(wanted) > 1 and wanted[-1:].isalpha() and wanted[:-1].isdigit():
            variants.add(f"{wanted[:-1]} {wanted[-1]}")
        return any(
            normalized_text.startswith(f"{variant}.")
            or normalized_text.startswith(f"{variant} ")
            for variant in variants
        )

    return any(
        lookup.payload_ir is not None
        and any(
            (
                child.kind is IRNodeKind.SUBSECTION
                and (
                    (child.label is not None and _norm_num_token(child.label) == wanted)
                    or _text_starts_with_entry_label(_node_text(child))
                )
            )
            or (
                child.kind is IRNodeKind.SUBSECTION
                and any(
                    grandchild.kind is IRNodeKind.PARAGRAPH
                    and grandchild.label is not None
                    and _norm_num_token(grandchild.label) == wanted
                    for grandchild in child.children
                )
            )
            for child in lookup.payload_ir.children
        )
        for lookup in lookups
    )


def _item_as_subsection_rewritten_ops(
    request: CompileGroupScopeRecoveryRequest,
    result: CompileGroupScopeRecoveryResult,
) -> tuple[list[AmendmentOp], tuple[str, ...]]:
    section = request.master.find_section(
        request.target_norm,
        result.effective_target_chapter,
    )
    if section is None:
        return result.group_ops, ()
    rewritten: list[AmendmentOp] = []
    rewritten_op_ids: list[str] = []
    for op in result.group_ops:
        item_label = _norm_num_token(op.target_item or "")
        if not (
            op.target_unit_kind == "section"
            and _norm_num_token(op.target_section or "") == request.target_norm
            and op.target_item
            and op.target_paragraph is not None
            and op.target_subitem is None
            and op.target_special is None
            and _section_is_definition_entry_list(section)
            and _source_payload_has_subsection_label(
                request.source_model,
                target_norm=request.target_norm,
                target_chapter=result.surface_target_chapter,
                target_part=result.surface_target_part,
                subsection_label=item_label,
            )
            and (
                _section_has_subsection_label(section, item_label)
                or (
                    op.op_type == "INSERT"
                    and not _section_subsection_has_paragraph_children(
                        section,
                        op.target_paragraph,
                    )
                )
            )
            and not _section_subsection_has_paragraph_children(section, op.target_paragraph)
        ):
            rewritten.append(op)
            continue
        rewritten_lo = (
            _lo_with_path_update(op.lo, subsection=item_label, item=None, subitem=None)
            if op.lo is not None
            else op.lo
        )
        rewritten.append(
            dc_replace(
                op,
                target_paragraph=None,
                target_item=None,
                target_subitem=None,
                lo=rewritten_lo,
            )
        )
        rewritten_op_ids.append(str(op.op_id or op.description()))
    return rewritten, tuple(rewritten_op_ids)


def _maybe_rewrite_item_targets_as_subsections(
    request: CompileGroupScopeRecoveryRequest,
    result: CompileGroupScopeRecoveryResult,
) -> PhaseResult[CompileGroupScopeRecoveryResult]:
    if request.target_unit_kind != "section":
        return PhaseResult(output=result)
    group_ops, rewritten_op_ids = _item_as_subsection_rewritten_ops(request, result)
    if not rewritten_op_ids:
        return PhaseResult(output=result)
    recovered = dc_replace(result, group_ops=group_ops)
    return PhaseResult(
        output=recovered,
        findings=(
            Finding(
                kind=_ITEM_AS_SUBSECTION_TARGET_REWRITE_RULE_ID,
                role="observation",
                stage=_STAGE,
                detail={
                    "rule_id": _ITEM_AS_SUBSECTION_TARGET_REWRITE_RULE_ID,
                    "phase": "lowering",
                    "family": "ontology_normalization",
                    "reason": "definition_section_source_and_live_encode_kohta_target_as_subsection_label",
                    "target_unit_kind": request.target_unit_kind,
                    "target_norm": request.target_norm,
                    "target_chapter": result.effective_target_chapter or "",
                    "target_part": result.effective_target_part or "",
                    "op_ids": rewritten_op_ids,
                    "strict_disposition": "allow",
                    "quirks_disposition": "apply",
                },
                source_statute=_source_statute(result.group_ops),
                blocking=False,
            ),
        ),
    )


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
    body_chapter_is_source_container = (
        body_chapter is not None
        and (
            request.source_model.body_has_real_chapter_container(body_chapter)
            or request.source_model.body_has_pseudo_chapter_marker(body_chapter)
        )
    )
    live_stem_host_scoped = group_has_scope_source(request.group_ops, "live_stem_host")
    combined_root_insert_range_owns_section = (
        body_chapter is not None
        and _combined_root_insert_range_owns_section(
            request.source_model,
            body_chapter=body_chapter,
            target_norm=request.target_norm,
        )
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
        and not (
            body_chapter_is_subchapter
            and body_chapter_is_inserted
            and live_stem_host_scoped
        )
        and _group_has_scope_that_overrides_body_wrapper(
            request.group_ops,
            target_chapter=request.target_chapter,
        )
    )
    body_wrapper_overridden_by_live_target = (
        body_chapter is not None
        and request.target_chapter is not None
        and body_chapter != request.target_chapter
        and _source_body_is_single_mixed_chapter_wrapper(
            request.source_model,
            body_chapter,
            request.master,
        )
        and _group_has_live_scoped_target_path(
            request.master,
            request.group_ops,
            target_norm=request.target_norm,
            target_chapter=request.target_chapter,
            target_part=request.target_part,
        )
    )
    source_owned_inserted_subchapter_scope = (
        body_chapter is not None
        and request.target_chapter is not None
        and body_chapter_is_subchapter
        and body_chapter_is_inserted
        and body_chapter_is_source_container
        and request.source_model.body_has_section(request.target_norm, target_chapter=body_chapter)
        and not carry_forward_scoped
    )
    source_owned_unscoped_inserted_chapter_scope = (
        body_chapter is not None
        and request.target_chapter is None
        and body_chapter_is_inserted
        and body_chapter_is_source_container
        and request.source_model.body_has_section(request.target_norm, target_chapter=body_chapter)
        and not explicit_chapter_scoped
        and not carry_forward_scoped
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
    source_owned_existing_chapter_insert_scope = (
        body_chapter is not None
        and request.target_chapter is not None
        and body_chapter == request.target_chapter
        and group_targets_whole_section
        and any(op.op_type == "INSERT" for op in request.group_ops)
        and not live_stem_host_scoped
        and request.source_model.body_has_real_chapter_container(body_chapter)
        and request.source_model.body_has_section(
            request.target_norm,
            target_chapter=body_chapter,
        )
    )
    source_owned_inserted_chapter_scope = (
        body_chapter is not None
        and request.target_chapter is not None
        and (
            source_owned_inserted_subchapter_scope
            or (
                (
                    (
                        body_chapter_is_inserted
                        and (
                            not live_stem_host_scoped
                            or combined_root_insert_range_owns_section
                        )
                    )
                    or body_chapter_is_empty_live_chapter
                )
                and not explicit_chapter_scoped
                and not carry_forward_scoped
            )
            or (
                carry_forward_scoped
                and not body_chapter_is_letter_suffix
                and body_chapter_is_inserted
            )
        )
        and body_chapter_is_source_container
        and not body_wrapper_overridden_by_scope
        and not body_wrapper_overridden_by_live_target
    )
    source_owned_existing_chapter_with_sibling_heading = (
        body_chapter is not None
        and live_stem_host_scoped
        and request.source_model.body_real_chapter_section_labels(body_chapter)
        == (_norm_num_token(request.target_norm),)
        and any(
            op.target_unit_kind == "chapter"
            and op.target_section
            and _norm_num_token(op.target_section) == _norm_num_token(body_chapter)
            and str(op.target_special or "").strip() == "otsikko"
            and op.op_type in {"REPLACE", "INSERT"}
            for op in request.amendment_group_ops
        )
    )
    source_body_letter_run_scope_corroborated = (
        body_chapter is not None
        and _source_body_letter_run_scope_is_corroborated(request, body_chapter)
    )
    source_owned_existing_letter_run_scope = (
        body_chapter is not None
        and request.target_chapter is not None
        and body_chapter != request.target_chapter
        and live_stem_host_scoped
        and group_targets_whole_section
        and any(op.op_type == "INSERT" for op in request.group_ops)
        and not explicit_chapter_scoped
        and not carry_forward_scoped
        and source_body_letter_run_scope_corroborated
    )
    source_owned_existing_chapter_scope = (
        body_chapter is not None
        and request.target_chapter is not None
        and body_chapter != request.target_chapter
        and not explicit_chapter_scoped
        and not carry_forward_scoped
        and (
            not group_has_scope_source(request.group_ops, "live_stem_host")
            or source_owned_existing_chapter_with_sibling_heading
        )
        and request.source_model.body_has_real_chapter_container(body_chapter)
        and request.source_model.body_has_section(request.target_norm, target_chapter=body_chapter)
        and not body_wrapper_overridden_by_scope
        and not body_wrapper_overridden_by_live_target
    )
    if body_chapter is not None and (
        not source_owned_inserted_subchapter_scope
        and not source_owned_unscoped_inserted_chapter_scope
        and not source_owned_existing_chapter_insert_scope
        and group_targets_whole_section
        and (
            not request.target_chapter
            or body_chapter_is_subchapter
            or (not explicit_chapter_scoped and body_chapter == request.target_chapter)
        )
        and not source_body_letter_run_scope_corroborated
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
        and request.target_chapter is not None
        and body_chapter != request.target_chapter
        and carry_forward_scoped
        and group_targets_whole_section
        and _group_has_witness_rule(
            request.group_ops,
            "fi_reinstated_section_scope_from_prior_repeal_address",
        )
    ):
        return PhaseResult(output=result)
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
        apply_correction = (
            request.master.find("chapter", resolved_body_chapter) is not None
            or source_owned_unscoped_inserted_chapter_scope
        )
    elif source_owned_inserted_chapter_scope:
        apply_correction = True
    elif source_owned_existing_chapter_scope:
        apply_correction = True
    elif source_owned_existing_letter_run_scope:
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
            body_chapter_move_from=(
                request.target_chapter if source_owned_existing_chapter_scope else None
            ),
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


def _source_body_letter_run_scope_is_corroborated(
    request: CompileGroupScopeRecoveryRequest,
    body_chapter: str,
) -> bool:
    target_match = re.fullmatch(r"(?P<stem>\d+)[a-z]+", request.target_norm, re.I)
    if target_match is None:
        return False
    if not request.source_model.body_has_real_chapter_container(body_chapter):
        return False
    if not request.source_model.body_has_section(
        request.target_norm,
        target_chapter=body_chapter,
    ):
        return False

    stem = target_match.group("stem")
    target_norm = _norm_num_token(request.target_norm)
    for sibling_label in request.source_model.body_real_chapter_section_labels(body_chapter):
        sibling_norm = _norm_num_token(sibling_label)
        if sibling_norm == target_norm:
            continue
        if re.fullmatch(rf"{re.escape(stem)}[a-z]+", sibling_norm, re.I) is None:
            continue
        if request.master.find_section_path(
            sibling_norm,
            body_chapter,
            request.target_part,
        ) is not None:
            return True
    return False


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


def _johto_declares_move_destination(
    *,
    johto: str,
    section_label: str,
    destination_chapter: str,
) -> bool:
    destination_norm = _norm_num_token(destination_chapter).removesuffix("luku")
    section_norm = _norm_num_token(section_label)
    mentions = collect_johto_chapter_scope_mentions(johto)
    return any(
        _norm_num_token(moved.section_label) == section_norm
        and _norm_num_token(moved.destination_chapter_label).removesuffix("luku")
        == destination_norm
        for moved in mentions.moved_section_destinations
    )


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
    if _johto_declares_move_destination(
        johto=request.johto,
        section_label=request.target_norm,
        destination_chapter=body_chapter,
    ):
        finding = Finding(
            kind=_BODY_CHAPTER_DECLARED_MOVE_RULE_ID,
            role="observation",
            stage=_STAGE,
            detail={
                "rule_id": _BODY_CHAPTER_DECLARED_MOVE_RULE_ID,
                "phase": "lowering",
                "family": "action_family_recovery",
                "reason": "johtolause_declares_same_label_section_move_destination",
                "original_action": "REPLACE",
                "lowered_action": "REPLACE",
                "target_unit_kind": request.target_unit_kind,
                "target_norm": request.target_norm,
                "target_chapter": request.target_chapter,
                "target_part": request.target_part or "",
                "body_chapter": body_chapter,
                "trigger_evidence": trigger_evidence,
                "op_ids": tuple(str(op.op_id or "") for op in replacement_ops),
                "strict_disposition": "allow",
                "quirks_disposition": "record",
            },
            source_statute=_source_statute(replacement_ops),
            blocking=False,
        )
        recovered = dc_replace(
            result,
            effective_target_chapter=body_chapter,
            surface_target_chapter=body_chapter,
            group_ops=_replace_to_declared_move_ops(
                result.group_ops,
                target_norm=request.target_norm,
                target_chapter=request.target_chapter,
                replacement_chapter=body_chapter,
            ),
        )
        return PhaseResult(output=recovered, findings=(finding,))
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
                target_subsection=_op_target_subsection_label(op),
                target_item=op.target_item,
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
    source_owned_existing_chapter_insert_scope = False
    body_scope = request.source_model.source_body_scope_for_section_target(request.target_norm)
    if body_scope is not None:
        body_part, body_chapter = body_scope
        source_owned_existing_chapter_insert_scope = (
            body_part == request.target_part
            and body_chapter == request.target_chapter
            and group_targets_whole_section
            and any(op.op_type == "INSERT" for op in request.group_ops)
            and not group_has_scope_source(request.group_ops, "live_stem_host")
            and body_chapter is not None
            and request.source_model.body_has_real_chapter_container(body_chapter)
            and request.source_model.body_has_section(
                request.target_norm,
                target_chapter=body_chapter,
            )
        )
    if (
        scoped_path is None
        and request.target_norm in request.master.duplicate_section_labels
        and group_targets_whole_section
        and not source_owned_existing_chapter_insert_scope
    ):
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
    if (
        retarget_scope_source == "explicit_chunk"
        and request.target_chapter
        and _johtolause_explicitly_binds_chapter_section(
            request.johto,
            request.target_chapter,
            request.target_norm,
        )
    ):
        # Source-owned chapter chunks beat broad amendment-body wrapper
        # membership when they explicitly bind this section.
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
                target_subsection=_op_target_subsection_label(op),
                target_item=op.target_item,
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
    item_rewrite_result = _maybe_rewrite_item_targets_as_subsections(request, result)
    result = item_rewrite_result.output
    findings += item_rewrite_result.findings()
    return PhaseResult(output=result, findings=findings)

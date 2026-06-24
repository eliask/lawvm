"""Stage 2 payload elaboration for Finland compile groups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from lawvm.core.compile_result import SourcePathology, StrictProfile
from lawvm.core.elaboration_context import (
    ReplayLookups,
    TargetContext,
    TargetUnitKind,
    build_payload_elaboration_context,
)
from lawvm.core.ir import IRNode
from lawvm.core.payload_surface import GroupSurface, PayloadSurface, build_payload_surface
from lawvm.core.payload_elaboration import PayloadCompletenessWitness
from lawvm.core.phase_result import Finding, PhaseBuilder, PhaseResult
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.constraints import _FilterCtx, _filter_ops_by_constraints
from lawvm.finland.elaborated_group import ElaboratedGroup, build_elaborated_group
from lawvm.finland.group_ops import (
    normalize_group_ops_for_repeal_reenact,
    remap_body_root_replace_group_before_terminal_voimaantulo,
)
from lawvm.finland.helpers import _norm_num_token, _norm_row_anchor_text
from lawvm.finland.ops import AmendmentOp, OpType, FailedOp, ReplayProfile
from lawvm.finland.sparse_tail_claims import (
    SPARSE_OMISSION_TAIL_PRUNE_RULE,
    SparseOmissionTailClaim,
    prune_sparse_tail_claims_from_carrier,
)
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.standalone_targets import StandaloneSectionTarget
from lawvm.finland.compile_group_surface import (
    collect_recodification_omission_only_section_shell_pathologies,
)
from lawvm.finland.payload_normalize import (
    _rewrite_internal_ordered_list_inserts,
    elaborate_payload_against_live,
    prepare_payload_surface,
)
from lawvm.finland.replay_findings import _strict_rejected_source_pathology_finding

_PAYLOAD_NORMALIZATION_RULE_ATTR = "lawvm_payload_normalization_rule"
_RESTORE_HEADING_FOR_EXPLICIT_FACET = "ELAB.RESTORE_HEADING_FOR_EXPLICIT_FACET"
_SOURCE_COMPLETE_CONTAINER_REPLACEMENT_RULE = "source_complete_container_replacement"


def _has_recodification_transfer_context(
    *,
    johto: str,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
) -> bool:
    if target_unit_kind not in {"chapter", "part"}:
        return False
    lowered = johto.lower()
    if "siirret" not in lowered:
        return False
    compact_johto = "".join(lowered.split())
    compact_target = target_norm.lower().replace(" ", "")
    return bool(compact_target) and compact_target in compact_johto


def _internal_replay_scope_row(
    *,
    source_statute: str,
    target_unit_kind: str,
    target_norm: str,
    target_chapter: str | None,
) -> dict[str, object]:
    """Canonical neutral replay-meta scope row for internal Finland reporting."""
    return {
        "source_statute": source_statute,
        "target_unit_kind": target_unit_kind,
        "target_norm": target_norm,
        "target_chapter": str(target_chapter or ""),
    }


def _internal_elaboration_observation_row(
    *,
    kind: str,
    stage: str,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    detail: dict[str, object],
) -> dict[str, object]:
    return {
        "kind": kind,
        "stage": stage,
        "detail": dict(detail),
        **_internal_replay_scope_row(
            source_statute=source_statute,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
        ),
    }


def _restore_source_heading_for_explicit_heading_facet(
    *,
    source_model: AmendmentSourceModel | None,
    prepared_muutos_ir: IRNode | None,
    group_ops: list[AmendmentOp],
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
) -> tuple[IRNode | None, dict[str, object] | None]:
    """Keep a typed source heading available for an explicit heading-facet op.

    Sparse subsection preparation may project a section payload down to the
    targeted subsection and remove the heading. When the johtolause also names
    the same section's ``otsikko``, the heading is source-owned by that explicit
    facet op; copy it from the typed source-model payload rather than guessing
    from prose or widening the subsection payload.
    """
    if (
        target_unit_kind != "section"
        or source_model is None
        or prepared_muutos_ir is None
        or prepared_muutos_ir.kind is not IRNodeKind.SECTION
    ):
        return prepared_muutos_ir, None
    if any(child.kind is IRNodeKind.HEADING for child in prepared_muutos_ir.children):
        return prepared_muutos_ir, None

    has_heading_op = any(
        op.target_unit_kind == "section"
        and _norm_num_token(str(op.target_section or target_norm or ""))
        == _norm_num_token(str(target_norm or ""))
        and str(op.target_special or "") == "otsikko"
        for op in group_ops
    )
    if not has_heading_op:
        return prepared_muutos_ir, None

    lookup = source_model.lookup_payload_ir(
        target_unit_kind,
        target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
    )
    source_payload = lookup.payload_ir
    if source_payload is None:
        return prepared_muutos_ir, None
    source_heading = next(
        (child for child in source_payload.children if child.kind is IRNodeKind.HEADING),
        None,
    )
    if source_heading is None:
        return prepared_muutos_ir, None

    children = list(prepared_muutos_ir.children)
    insert_at = 1 if children and children[0].kind is IRNodeKind.NUM else 0
    children.insert(insert_at, source_heading)
    restored = IRNode(
        kind=prepared_muutos_ir.kind,
        label=prepared_muutos_ir.label,
        text=prepared_muutos_ir.text,
        attrs={
            **dict(prepared_muutos_ir.attrs),
            _PAYLOAD_NORMALIZATION_RULE_ATTR: _RESTORE_HEADING_FOR_EXPLICIT_FACET,
        },
        children=tuple(children),
    )
    return restored, {
        "target_unit_kind": target_unit_kind,
        "target_norm": target_norm,
        "source_payload_status": lookup.status,
        "heading_text_chars": len(str(source_heading.text or "")),
    }


def drop_payloadless_source_replace_shadowed_by_same_group_relabel(
    group_ops: list[AmendmentOp],
    *,
    muutos_ir: IRNode | None,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
) -> tuple[list[AmendmentOp], list[FailedOp]]:
    """Reject payloadless whole-section REPLACE ops shadowed by same-group relabel."""
    if muutos_ir is not None or target_unit_kind != "section":
        return group_ops, []
    if not any(op.op_type == OpType.RENUMBER for op in group_ops):
        return group_ops, []

    kept_ops: list[AmendmentOp] = []
    rejected_ops: list[FailedOp] = []
    for op in group_ops:
        if (
            op.op_type == OpType.REPLACE
            and op.target_unit_kind == "section"
            and _norm_num_token(op.target_section or "") == target_norm
            and op.target_paragraph is None
            and not op.target_item
            and not op.target_special
            and op.target_chapter == target_chapter
            and op.target_part == target_part
        ):
            rejected_ops.append(
                FailedOp.from_scope(
                    amendment_id=str(op.source_statute or ""),
                    description=str(op.description()),
                    reason="payloadless_source_replace_shadowed_by_relabel",
                    reason_code="ELAB.PAYLOADLESS_REPLACE_SHADOWED_BY_RELABEL",
                    target_section=target_norm,
                    target_unit_kind="section",
                    target_chapter=target_chapter,
                    target_part=target_part,
                )
            )
            continue
        kept_ops.append(op)
    return kept_ops, rejected_ops


def _source_complete_container_replacement_witness(
    *,
    raw_muutos_ir: IRNode | None,
    group_ops: list[AmendmentOp],
    target_unit_kind: TargetUnitKind,
    target_norm: str,
) -> PayloadCompletenessWitness | None:
    """Complete-source witness for whole container replacements.

    Container elaboration may merge live children into a chapter/part payload so
    replay has enough context to execute local section updates. The raw source
    payload still owns the authoritative child set for a whole-container REPLACE;
    carry that set explicitly so timeline snapshotting does not treat live-merged
    children as source-owned.
    """
    if target_unit_kind not in {"chapter", "part"}:
        return None
    expected_kind = IRNodeKind.CHAPTER if target_unit_kind == "chapter" else IRNodeKind.PART
    if raw_muutos_ir is None or raw_muutos_ir.kind is not expected_kind:
        return None
    if raw_muutos_ir.label and _norm_num_token(raw_muutos_ir.label) != target_norm:
        return None
    whole_replaces = [
        op
        for op in group_ops
        if op.op_type == OpType.REPLACE
        and op.target_unit_kind == target_unit_kind
        and op.target_paragraph is None
        and not op.target_item
        and (not op.target_special or op.target_special in {"otsikko", "otsikko_edella"})
    ]
    if len(whole_replaces) != 1:
        return None
    child_kind = IRNodeKind.SECTION if target_unit_kind == "chapter" else IRNodeKind.CHAPTER
    child_labels = tuple(
        sorted(
            {
                _norm_num_token(child.label)
                for child in raw_muutos_ir.children
                if child.kind is child_kind and child.label
            }
        )
    )
    if not child_labels:
        return None
    return PayloadCompletenessWitness(
        kind="complete",
        reasons=(_SOURCE_COMPLETE_CONTAINER_REPLACEMENT_RULE,),
        tail_policy="replace_if_target_scope_requires",
        detail={"source_child_labels": child_labels},
    )


def _merge_source_container_replacement_witness(
    payload_completeness: PayloadCompletenessWitness | None,
    source_witness: PayloadCompletenessWitness | None,
) -> PayloadCompletenessWitness | None:
    """Attach raw-source container child ownership to compatible completeness."""
    if source_witness is None:
        return payload_completeness
    if payload_completeness is None:
        return source_witness
    if (
        payload_completeness.kind != "complete"
        or payload_completeness.tail_policy != "replace_if_target_scope_requires"
    ):
        return payload_completeness
    reasons = tuple(
        dict.fromkeys((*payload_completeness.reasons, *source_witness.reasons))
    )
    detail = {
        **dict(payload_completeness.detail or {}),
        **dict(source_witness.detail or {}),
    }
    return PayloadCompletenessWitness(
        kind=payload_completeness.kind,
        reasons=reasons,
        tail_policy=payload_completeness.tail_policy,
        detail=detail,
    )


_REJECTED_OPERATION_MESSAGE = "operation rejected before apply"


def rejected_operation_findings(failed_ops: Iterable[Any], stage: str) -> list[Finding]:
    """Paired rejected-before-apply findings for a batch of failed ops."""
    findings: list[Finding] = []
    for failed in failed_ops:
        findings.append(
            Finding(
                kind="ELAB.REJECTED_OPERATION",
                role="observation",
                stage=stage,
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
                stage=stage,
                detail={**failed.as_detail(), "message": _REJECTED_OPERATION_MESSAGE},
                source_statute=failed.amendment_id,
                blocking=True,
            )
        )
    return findings


def _payload_normalization_observation_rows(
    muutos_ir: IRNode | None,
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if muutos_ir is None:
        return rows

    def walk(node: IRNode, path: tuple[str, ...]) -> None:
        raw_rules = node.attrs.get(_PAYLOAD_NORMALIZATION_RULE_ATTR, ())
        rules = tuple(raw_rules) if isinstance(raw_rules, tuple) else ((str(raw_rules),) if raw_rules else ())
        for rule in rules:
            if not rule:
                continue
            rows.append(
                _internal_elaboration_observation_row(
                    kind=rule,
                    stage="group_payload_normalization",
                    detail={
                        "rule": rule,
                        "payload_path": "/".join((*path, f"{node.kind.value}:{node.label or ''}")),
                    },
                    source_statute=source_statute,
                    target_unit_kind=target_unit_kind,
                    target_norm=target_norm,
                    target_chapter=target_chapter,
                )
            )
        child_prefix = (*path, f"{node.kind.value}:{node.label or ''}")
        for child in node.children:
            walk(child, child_prefix)

    walk(muutos_ir, ())
    return rows


@dataclass(frozen=True, slots=True)
class ElaborateGroupRequest:
    """Typed inputs for compile-group payload elaboration."""

    target_ctx: TargetContext
    lookups: ReplayLookups
    group_surface: GroupSurface
    group_ops: list[AmendmentOp]
    standalone_section_targets: set[str]
    foreign_scoped_standalone_section_targets: set[str]
    foreign_scoped_replace_section_targets: set[str]
    effective_target_part: str | None
    source_model: AmendmentSourceModel
    johto: str
    profile: ReplayProfile
    strict_profile: Optional[StrictProfile]
    foreign_scoped_descendant_section_targets: set[str] = field(default_factory=set)
    foreign_scoped_replace_section_target_scopes: frozenset[StandaloneSectionTarget] = frozenset()
    sparse_omission_tail_claims: tuple[SparseOmissionTailClaim, ...] = ()


def elaborate_group(request: ElaborateGroupRequest) -> PhaseResult[ElaboratedGroup]:
    """Stage 2: elaborate payload against live state."""
    target_ctx = request.target_ctx
    lookups = request.lookups
    group_surface = request.group_surface
    group_ops = request.group_ops
    standalone_section_targets = request.standalone_section_targets
    foreign_scoped_standalone_section_targets = (
        request.foreign_scoped_standalone_section_targets
    )
    foreign_scoped_replace_section_targets = request.foreign_scoped_replace_section_targets
    foreign_scoped_descendant_section_targets = (
        request.foreign_scoped_descendant_section_targets
    )
    foreign_scoped_replace_section_target_scopes = (
        request.foreign_scoped_replace_section_target_scopes
    )
    target_part = request.effective_target_part
    source_model = request.source_model
    johto = request.johto
    profile = request.profile
    strict_profile = request.strict_profile

    target_unit_kind = group_surface.target_unit_kind
    target_norm = group_surface.target_norm
    target_chapter = group_surface.target_chapter
    observation_source_statute = group_surface.source_statute
    muutos_ir = group_surface.body_ir
    raw_muutos_ir = muutos_ir
    payload_ctx = build_payload_elaboration_context(
        target_ctx,
        lookups,
        row_anchor_normalizer=_norm_row_anchor_text,
    )
    muutos_ir, pre_prepare_pruned_sparse_tail_claims = prune_sparse_tail_claims_from_carrier(
        muutos_ir,
        request.sparse_omission_tail_claims,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
    )
    pre_prepare_observations: list[dict[str, object]] = []
    if pre_prepare_pruned_sparse_tail_claims:
        pre_prepare_observations.append(
            _internal_elaboration_observation_row(
                kind=SPARSE_OMISSION_TAIL_PRUNE_RULE,
                stage="group_payload_normalization",
                detail={
                    "target_unit_kind": target_unit_kind,
                    "target_norm": target_norm,
                    "target_chapter": target_chapter or "",
                    "pruned_claims": [
                        claim.detail() for claim in pre_prepare_pruned_sparse_tail_claims
                    ],
                },
                source_statute=observation_source_statute,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter,
            )
        )
    muutos_ir = prepare_payload_surface(
        payload_ctx,
        group_ops,
        muutos_ir,
        profile,
        strict_profile,
    )
    group_ops, muutos_ir, internal_list_observation = _rewrite_internal_ordered_list_inserts(
        payload_ctx,
        target_unit_kind,
        muutos_ir,
        group_ops,
    )
    if internal_list_observation is not None:
        pre_prepare_observations.append(
            _internal_elaboration_observation_row(
                kind=str(internal_list_observation.kind or ""),
                stage=str(internal_list_observation.stage or ""),
                detail=dict(internal_list_observation.detail or {}),
                source_statute=observation_source_statute,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter,
            )
        )
    heading_restore_observation: dict[str, object] | None = None
    muutos_ir, heading_restore_observation = _restore_source_heading_for_explicit_heading_facet(
        source_model=source_model,
        prepared_muutos_ir=muutos_ir,
        group_ops=group_ops,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
    )
    prepared_payload_observations = _payload_normalization_observation_rows(
        muutos_ir,
        source_statute=observation_source_statute,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
    )
    surface: PayloadSurface = build_payload_surface(
        muutos_ir,
        group_surface.cross_heading_ir,
        source_statute=observation_source_statute,
    )

    local_rejected_ops: list[FailedOp] = []
    fctx = _FilterCtx(
        muutos_ir=muutos_ir,
        johto=johto,
        source_model=source_model,
    )
    group_ops = _filter_ops_by_constraints(group_ops, fctx, rejected_ops_out=local_rejected_ops)
    group_ops, shadowed_replace_rejections = drop_payloadless_source_replace_shadowed_by_same_group_relabel(
        group_ops,
        muutos_ir=muutos_ir,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
    )
    local_rejected_ops.extend(shadowed_replace_rejections)
    if not group_ops:
        elaborated = build_elaborated_group(
            muutos_ir=None,
            cross_ir=group_surface.cross_heading_ir,
            group_ops=[],
            remapped_target_norm=target_norm,
            slot_assignment=None,
            was_filtered=True,
            payload_surface=surface,
            payload_completeness=None,
        )
        b = PhaseBuilder()
        b.add_findings(
            Finding(
                kind="ELAB.STRICT_REJECTED_OPERATION",
                role="obligation",
                stage="_elaborate_group",
                detail={**failed.as_detail(), "message": "operation rejected before apply"},
                source_statute=failed.amendment_id,
                blocking=True,
            )
            for failed in local_rejected_ops
        )
        return b.finish(elaborated)

    payload_norm = elaborate_payload_against_live(
        payload_ctx,
        group_ops,
        muutos_ir,
        standalone_section_targets,
        foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
        foreign_scoped_descendant_section_targets=foreign_scoped_descendant_section_targets,
        foreign_scoped_replace_section_targets=foreign_scoped_replace_section_targets,
        foreign_scoped_replace_section_target_scopes=foreign_scoped_replace_section_target_scopes,
        recodification_transfer_context=_has_recodification_transfer_context(
            johto=johto,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
        ),
        sparse_omission_tail_claims=request.sparse_omission_tail_claims,
        surface=surface,
    )
    muutos_ir = payload_norm.muutos_ir
    group_ops = list(payload_norm.group_ops)
    payload_completeness = _merge_source_container_replacement_witness(
        payload_norm.payload_completeness,
        _source_complete_container_replacement_witness(
            raw_muutos_ir=raw_muutos_ir,
            group_ops=group_ops,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
        ),
    )

    local_source_pathologies: list[SourcePathology] = list(payload_norm.source_pathologies or [])
    local_source_pathologies.extend(
        collect_recodification_omission_only_section_shell_pathologies(
            group_ops=group_ops,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            source_model=source_model,
        )
    )
    local_elaboration_observations: list[dict[str, object]] = [
        *pre_prepare_observations,
        *prepared_payload_observations,
        *[
        _internal_elaboration_observation_row(
            kind=str(observation.kind or ""),
            stage=str(observation.stage or ""),
            detail=dict(observation.detail or {}),
            source_statute=observation_source_statute,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
        )
        for observation in (payload_norm.elaboration_observations or [])
        if str(observation.kind or "").strip()
        ],
    ]
    if heading_restore_observation is not None:
        local_elaboration_observations.append(
            _internal_elaboration_observation_row(
                kind=_RESTORE_HEADING_FOR_EXPLICIT_FACET,
                stage="group_payload_normalization",
                detail=heading_restore_observation,
                source_statute=observation_source_statute,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter,
            )
        )
    slot_assignment = payload_norm.slot_assignment
    local_payload_completeness: list[dict[str, object]] = (
        [
            _internal_elaboration_observation_row(
                kind="ELAB.PAYLOAD_COMPLETENESS",
                stage="group_payload_normalization",
                detail={
                    "payload_completeness_kind": str(payload_completeness.kind or ""),
                    "reasons": list(payload_completeness.reasons or []),
                    "tail_policy": str(payload_completeness.tail_policy or ""),
                    **dict(payload_completeness.detail or {}),
                },
                source_statute=observation_source_statute,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter,
            )
        ]
        if payload_completeness is not None
        else []
    )
    local_sparse_slot_bindings: list[dict[str, object]] = [
        {
            **_internal_replay_scope_row(
                source_statute=observation_source_statute,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter,
            ),
            "op_description": binding.op_description,
            "op_type": binding.op_type,
            "target_paragraph": binding.target_paragraph,
            "target_item": binding.target_item or "",
            "target_special": binding.target_special or "",
            "payload_slot_index": binding.payload_slot_index,
            "payload_slot_label": binding.payload_slot_label,
        }
        for binding in (slot_assignment.sparse_slot_bindings if slot_assignment is not None else [])
    ]
    local_sparse_leftovers: list[dict[str, object]] = (
        [
            {
                **_internal_replay_scope_row(
                    source_statute=observation_source_statute,
                    target_unit_kind=target_unit_kind,
                    target_norm=target_norm,
                    target_chapter=target_chapter,
                ),
                "unassigned_slots": list(slot_assignment.unassigned_payload_slots),
            }
        ]
        if slot_assignment is not None and slot_assignment.unassigned_payload_slots
        else []
    )
    local_rejected_ops.extend(payload_norm.rejected_ops or ())
    local_strict_rejection_findings: list[Finding] = []
    if strict_profile is not None and payload_norm.source_pathologies:
        local_strict_rejection_findings.extend(
            _strict_rejected_source_pathology_finding(
                pathology,
                stage="_elaborate_group",
                fallback_source_statute=observation_source_statute,
            )
            for pathology in payload_norm.source_pathologies
        )

    if not group_ops:
        elaborated = build_elaborated_group(
            muutos_ir=None,
            cross_ir=group_surface.cross_heading_ir,
            group_ops=[],
            remapped_target_norm=target_norm,
            slot_assignment=None,
            was_filtered=True,
            payload_surface=surface,
            payload_completeness=payload_completeness,
        )
    else:
        fctx.slot_assignment = slot_assignment
        group_ops = _filter_ops_by_constraints(group_ops, fctx, rejected_ops_out=local_rejected_ops)
        group_ops = normalize_group_ops_for_repeal_reenact(group_ops)
        remapped_target_norm, muutos_ir, group_ops = remap_body_root_replace_group_before_terminal_voimaantulo(
            target_ctx, lookups, muutos_ir, group_ops
        )
        elaborated = build_elaborated_group(
            muutos_ir=muutos_ir,
            cross_ir=group_surface.cross_heading_ir,
            group_ops=group_ops,
            remapped_target_norm=remapped_target_norm,
            slot_assignment=slot_assignment,
            source_pathologies=local_source_pathologies,
            was_filtered=False,
            payload_surface=surface,
            payload_completeness=payload_completeness,
        )

    b = PhaseBuilder()
    b.add_findings(rejected_operation_findings(local_rejected_ops, "_elaborate_group"))
    b.add_findings(
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role="observation",
            stage="_elaborate_group",
            detail=p.as_detail(),
            source_statute=p.source_statute or observation_source_statute,
            blocking=False,
        )
        for p in local_source_pathologies
    )
    b.add_findings(
        Finding(
            kind=str(o.get("kind", "")),
            role="observation",
            stage="_elaborate_group",
            detail=dict(o),
            source_statute=str(o.get("source_statute", observation_source_statute)),
            blocking=False,
        )
        for o in local_elaboration_observations
        if str(o.get("kind", "")).strip()
    )
    b.add_findings(
        Finding(
            kind="ELAB.PAYLOAD_COMPLETENESS",
            role="observation",
            stage="_elaborate_group",
            detail=dict(witness),
            source_statute=str(witness.get("source_statute", observation_source_statute)),
            blocking=False,
        )
        for witness in local_payload_completeness
    )
    b.add_findings(
        Finding(
            kind="ELAB.SPARSE_SLOT_BINDING",
            role="observation",
            stage="_elaborate_group",
            detail=dict(binding),
            source_statute=str(binding.get("source_statute", observation_source_statute)),
            blocking=False,
        )
        for binding in local_sparse_slot_bindings
    )
    b.add_findings(
        Finding(
            kind="ELAB.SPARSE_PAYLOAD_LEFTOVER",
            role="obligation",
            stage="_elaborate_group",
            detail=dict(leftover),
            blocking=False,
        )
        for leftover in local_sparse_leftovers
    )
    b.add_findings(local_strict_rejection_findings)

    return b.finish(elaborated)


_drop_payloadless_source_replace_shadowed_by_same_group_relabel = (
    drop_payloadless_source_replace_shadowed_by_same_group_relabel
)
_rejected_operation_findings = rejected_operation_findings
_ElaborateGroupRequest = ElaborateGroupRequest
_elaborate_group = elaborate_group

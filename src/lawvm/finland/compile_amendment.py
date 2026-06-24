"""Amendment-level compilation for Finland replay."""

from __future__ import annotations

import dataclasses
from typing import Any, Literal, Optional, cast

from lawvm.core.compile_result import StrictProfile
from lawvm.core.effect_lowering import lower_effect_intents_to_temporal_events
from lawvm.core.elaboration_context import TargetUnitKind, snapshot_replay_lookups
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.core.stage_result import (
    EMPTY_EVIDENCE,
    NEUTRAL_AUTHORITY,
    CoverageCertificate,
    Residual,
    StageResult,
)
from lawvm.core.temporal import ActivationRule, TemporalEvent
from lawvm.finland.compile_group import compile_group_typed as _compile_group_typed
from lawvm.finland.compile_group_boundary import CompileGroupRequest, CompileGroupSinks
from lawvm.finland.compile_group_scope_recovery import (
    CompileGroupScopeRecoveryRequest,
    resolve_compile_group_scope_recovery,
)
from lawvm.finland.effect_lowering import UnsupportedMetaClause, lower_johto_effects
from lawvm.finland.group_plan import (
    coalesce_same_target_mixed_scope_section_groups,
    group_ops_by_target,
)
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses
from lawvm.finland.ops import AmendmentOp, ResolvedOp, get_replay_profile
from lawvm.finland.sparse_tail_claims import build_sparse_omission_tail_claims
from lawvm.finland.source_model import AmendmentSourceModel
from lawvm.finland.standalone_targets import (
    group_shadow_pruning_foreign_scoped_descendant_section_targets,
    group_shadow_pruning_foreign_scoped_replace_section_targets,
    group_shadow_pruning_foreign_scoped_replace_section_target_scopes,
    group_shadow_pruning_foreign_scoped_section_targets,
    group_shadow_pruning_section_targets,
)
from lawvm.finland.statute import ReplayState
from lawvm.finland.temporal_lowering import (
    activation_rules_from_meta_clauses_with_findings,
    classify_contingent,
    default_activation_rule,
)

_NUMBERED_TABLE_CHILD_GROUP_SPLIT_RULE = "ELAB.NUMBERED_TABLE_CHILD_GROUP_SPLIT"


@dataclasses.dataclass(frozen=True, slots=True)
class _CanonicalOpResidualCarrier:
    """One blocking canonical-op residual paired with the Finding it carries.

    WAIST #6 single-channel (ESCALATE-5D): the canonical-op decline must ride the
    typed ``StageResult.residuals`` as its SOLE source, not a parallel
    ``PhaseResult`` obligation. To make that true without losing the registered
    Finding's identity/detail (0-delta), the typed residual CARRIES the exact
    blocking Finding it represents. The returned decline is then reconstructed by
    reading the carried Finding back OUT of the residual — so stripping the
    residual list removes the decline (the fire-drill bite).
    """

    residual: Residual
    finding: Finding


def build_canonical_op_stage(
    resolved: list[ResolvedOp],
    findings: list[Finding],
) -> tuple[StageResult[list[ResolvedOp]], tuple[_CanonicalOpResidualCarrier, ...]]:
    """Assemble the canonical-op ``StageResult`` for ``compile_amendment_ops``.

    The blocking findings (obligations/violations — the decline channel) are
    projected onto typed blocking ``Residual`` records; the observation findings
    stay as ``StageResult.findings``. ESCALATE-3D coverage denominator:
    ``total = #emitted resolved ops + #rejected (blocking) candidate ops`` — the
    rejected lane is the producer's own typed blocking findings, reusing the
    existing typed partition rather than a synthetic source recount.

    Returns the ``StageResult`` AND the residual<->finding carriers so the caller
    can reconstruct the returned blocking decline FROM the typed residuals (the
    single-channel guarantee).
    """
    observations: list[Finding] = []
    carriers: list[_CanonicalOpResidualCarrier] = []
    for finding in findings:
        if finding.role == "observation":
            observations.append(finding)
            continue
        detail = finding.detail
        message = str(detail.get("message", "") or "")
        carriers.append(
            _CanonicalOpResidualCarrier(
                residual=Residual(
                    kind="unowned_violation",
                    reason=(
                        message
                        or f"{finding.kind}: rejected canonical operation"
                    ),
                    scope=finding.kind,
                    source_unit_id=finding.source_statute,
                    text=str(detail.get("target_section", "") or ""),
                    blocking=bool(finding.blocking),
                ),
                finding=finding,
            )
        )
    emitted = len(resolved)
    rejected = len(carriers)
    coverage = CoverageCertificate(
        unit="candidate_ops",
        total=emitted + rejected,
        owned=emitted,
        violation=rejected,
        totality_claimed=True,
    )
    residuals = [carrier.residual for carrier in carriers]
    # XP-03 — op-coverage totality (runtime parity arm). Every candidate operation
    # MUST lower to exactly one canonical op (``owned``) OR a typed candidate-effect
    # residual (``violation``); none may be silently dropped. That is exactly
    # ``coverage.is_partition()``. It holds BY CONSTRUCTION here (``total`` is
    # computed as ``emitted + rejected``), so the guard below is a defensive
    # runtime PIN: were a future producer to recompute the partition some other
    # way and leave an op unaccounted, the gap surfaces as a typed (non-blocking)
    # ``CANONICAL_OP.OP_COVERAGE_GAP`` residual rather than vanishing silently.
    if not coverage.is_partition():
        residuals.append(
            _op_coverage_gap_residual(
                owned=emitted, violation=rejected, total=coverage.total
            )
        )
    stage = StageResult(
        value=resolved,
        evidence=EMPTY_EVIDENCE,
        residuals=tuple(residuals),
        findings=tuple(observations),
        coverage=coverage,
        authority=NEUTRAL_AUTHORITY,
    )
    return stage, tuple(carriers)


#: The XP-03 op-coverage-gap residual kind (also the registry code surfaced when
#: the candidate-op partition fails — see observation_registry).
OP_COVERAGE_GAP_RESIDUAL_KIND = "CANONICAL_OP.OP_COVERAGE_GAP"


def _op_coverage_gap_residual(*, owned: int, violation: int, total: int) -> Residual:
    """The XP-03 typed residual for a candidate op left unaccounted at lowering.

    Self-evidencing per the diagnostics rule: the reason embeds the offending
    partition counts so a reader sees WHY totality failed without re-deriving it.
    Non-blocking (``blocking=False``): a real uncovered op should be SURFACED for
    triage, never silently drop the whole amendment.
    """
    return Residual(
        kind=OP_COVERAGE_GAP_RESIDUAL_KIND,
        reason=(
            "candidate-op coverage is not a partition at the canonical-op lowering "
            f"waist: owned={owned} + violation={violation} != total={total} "
            "(a candidate operation neither lowered to a canonical op nor "
            "residualized)"
        ),
        scope="candidate_ops",
        blocking=False,
    )


def _is_numbered_table_proxy_op(op: AmendmentOp) -> bool:
    return (
        op.target_unit_kind == "section"
        and bool(op.numbered_table_targets)
        and op.target_paragraph is None
        and not op.target_item
        and not op.target_special
    )


def _has_child_target(op: AmendmentOp) -> bool:
    return op.target_paragraph is not None or bool(op.target_item) or bool(op.target_special)


def _split_numbered_table_child_group_ops(group_ops: list[AmendmentOp]) -> tuple[list[AmendmentOp], ...]:
    """Separate numbered-table proxies from explicit child ops in one section group.

    A numbered-table proxy is executable as a whole-section replace only after
    its payload has been rewritten to "live section plus amended table".  If it
    shares a group with paragraph/item ops, that proxy must not carry those
    child mutations.  Compile the proxy and child ops independently against the
    same source body so each op receives only its own target authority.
    """
    table_ops = [op for op in group_ops if _is_numbered_table_proxy_op(op)]
    child_ops = [op for op in group_ops if _has_child_target(op)]
    if not table_ops or not child_ops:
        return (group_ops,)

    remaining_ops = [op for op in group_ops if op not in table_ops]
    return (table_ops, remaining_ops)


def _numbered_table_child_group_split_finding(
    *,
    group_key: object,
    subgroups: tuple[list[AmendmentOp], ...],
    source_ref: str,
) -> Finding:
    table_op_ids = [
        op.op_id or op.target_section
        for group in subgroups
        for op in group
        if _is_numbered_table_proxy_op(op)
    ]
    child_op_ids = [
        op.op_id or f"{op.target_section}:{op.target_paragraph or ''}:{op.target_item or ''}"
        for group in subgroups
        for op in group
        if _has_child_target(op)
    ]
    return Finding(
        kind=_NUMBERED_TABLE_CHILD_GROUP_SPLIT_RULE,
        role="observation",
        stage="compile_amendment_ops",
        detail={
            "group_key": str(group_key),
            "table_op_ids": table_op_ids,
            "child_op_ids": child_op_ids,
        },
        source_statute=source_ref,
        blocking=False,
    )


def _scope_recovered_ops_for_shadow_pruning(
    master: ReplayState,
    section_groups: dict[Any, list[AmendmentOp]],
    *,
    inserted_chapter_labels: set[str],
    source_model: AmendmentSourceModel,
    johto: str,
    strict_profile: Optional[StrictProfile],
) -> list[AmendmentOp]:
    recovered_ops: list[AmendmentOp] = []
    amendment_group_ops = tuple(op for group_ops in section_groups.values() for op in group_ops)
    for group_key, group_ops in section_groups.items():
        target_unit_kind_value = cast(TargetUnitKind, group_key.unit_kind.value)
        recovery_result = resolve_compile_group_scope_recovery(
            CompileGroupScopeRecoveryRequest(
                master=master,
                target_unit_kind=target_unit_kind_value,
                target_norm=group_key.target_norm,
                target_chapter=group_key.target_chapter,
                target_part=group_key.target_part,
                group_ops=group_ops,
                inserted_chapter_labels=inserted_chapter_labels,
                source_model=source_model,
                johto=johto,
                strict_profile=strict_profile,
                amendment_group_ops=amendment_group_ops,
            )
        )
        recovery = recovery_result.output
        recovered_ops.extend(group_ops if recovery.blocked else recovery.group_ops)
    return recovered_ops


def compile_amendment_ops(
    master: ReplayState,
    ops: list[AmendmentOp],
    source_model: AmendmentSourceModel,
    johto: str,
    replay_mode: Literal["official_consolidation", "legal_pit"],
    compiled_ops_out: Optional[list[dict[str, object]]] = None,
    strict_profile: Optional[StrictProfile] = None,
    *,
    source_ref: str = "",
    source_title: str = "",
    target_statute: str = "",
    canonical_op_stage_out: Optional[list[StageResult[list[ResolvedOp]]]] = None,
) -> PhaseResult[list[ResolvedOp]]:
    """Compile grouped amendment ops into resolved ops ready for application.

    ``canonical_op_stage_out`` is the WAIST #6 carrier sink: when provided, the
    per-amendment canonical-op ``StageResult`` (the same ``stage`` that backs the
    typed-residual decline single-channel below) is APPENDED to it. This lets the
    replay assembly aggregate the per-amendment canonical-op accounts into one
    ``ReplayProducts.canonical_op_stage`` carrier WITHOUT re-deriving the partition
    from union findings (which carry no stage tag). The sink only OBSERVES the
    stage; the decline still rides ``reconstruct_findings_from_canonical_op_stage``
    (the load-bearing single-channel) unchanged.
    """
    profile = get_replay_profile(replay_mode)
    source_title = source_title or source_model.title()
    amendment_issue_date = source_model.issue_date()
    amendment_effective_date = source_model.effective_date()

    section_groups = coalesce_same_target_mixed_scope_section_groups(
        group_ops_by_target(ops),
        master=master,
        find_body_section_chapter=source_model.first_body_section_chapter,
    )
    inserted_chapter_labels = {
        _norm_num_token(op.target_section or "")
        for op in ops
        if op.target_unit_kind == "chapter" and op.op_type == "INSERT" and op.target_section
    }
    inserted_chapter_labels.update(
        _norm_num_token(source_chapter.chapter_label)
        for source_chapter in source_model.source_pseudo_chapters()
        if source_chapter.chapter_label
    )
    shadow_pruning_ops = _scope_recovered_ops_for_shadow_pruning(
        master,
        section_groups,
        inserted_chapter_labels=inserted_chapter_labels,
        source_model=source_model,
        johto=johto,
        strict_profile=strict_profile,
    )
    sparse_omission_tail_claims = build_sparse_omission_tail_claims(
        shadow_pruning_ops,
        source_model,
    )
    resolved: list[ResolvedOp] = []
    all_findings: list[Finding] = []

    precomputed_lookups = snapshot_replay_lookups(cast(Any, master))
    amendment_group_ops = tuple(op for ops in section_groups.values() for op in ops)

    for group_key, group_ops in section_groups.items():
        target_unit_kind_value = cast(TargetUnitKind, group_key.unit_kind.value)
        standalone_section_targets = group_shadow_pruning_section_targets(
            shadow_pruning_ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=group_key.target_norm,
            target_part=group_key.target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        foreign_scoped_standalone_section_targets = group_shadow_pruning_foreign_scoped_section_targets(
            shadow_pruning_ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=group_key.target_norm,
            target_part=group_key.target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        foreign_scoped_descendant_section_targets = group_shadow_pruning_foreign_scoped_descendant_section_targets(
            shadow_pruning_ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=group_key.target_norm,
            target_part=group_key.target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        foreign_scoped_replace_section_targets = group_shadow_pruning_foreign_scoped_replace_section_targets(
            shadow_pruning_ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=group_key.target_norm,
            target_part=group_key.target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        foreign_scoped_replace_section_target_scopes = (
            group_shadow_pruning_foreign_scoped_replace_section_target_scopes(
                shadow_pruning_ops,
                target_unit_kind=target_unit_kind_value,
                target_norm=group_key.target_norm,
                target_part=group_key.target_part,
                duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
            )
        )
        subgroups = _split_numbered_table_child_group_ops(group_ops)
        if len(subgroups) > 1:
            all_findings.append(
                _numbered_table_child_group_split_finding(
                    group_key=group_key,
                    subgroups=subgroups,
                    source_ref=source_ref,
                )
            )
        for subgroup_ops in subgroups:
            group_result = _compile_group_typed(
                CompileGroupRequest(
                    master=master,
                    target_unit_kind=target_unit_kind_value,
                    target_norm=group_key.target_norm,
                    target_chapter=group_key.target_chapter,
                    target_part=group_key.target_part,
                    group_ops=subgroup_ops,
                    standalone_section_targets=standalone_section_targets,
                    foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
                    foreign_scoped_descendant_section_targets=foreign_scoped_descendant_section_targets,
                    foreign_scoped_replace_section_targets=foreign_scoped_replace_section_targets,
                    foreign_scoped_replace_section_target_scopes=foreign_scoped_replace_section_target_scopes,
                    sparse_omission_tail_claims=sparse_omission_tail_claims,
                    inserted_chapter_labels=inserted_chapter_labels,
                    source_model=source_model,
                    johto=johto,
                    profile=profile,
                    strict_profile=strict_profile,
                    lookups=precomputed_lookups,
                    amendment_group_ops=amendment_group_ops,
                ),
                CompileGroupSinks(compiled_ops_out=compiled_ops_out),
            )
            resolved.extend(group_result.output)
            all_findings.extend(group_result.findings())

    lowered_temporal_events: tuple[TemporalEvent, ...] = ()
    activation_rules: list[ActivationRule] = []
    if johto:
        unsupported_meta_clauses: list[UnsupportedMetaClause] = []
        lowered_effect_intents = tuple(
            lower_johto_effects(
                johto,
                unsupported_out=unsupported_meta_clauses,
            )
        )
        all_findings.extend(
            Finding(
                kind=record.rule_id,
                role="observation",
                stage=record.phase,
                detail=record.as_detail(),
                source_statute=source_ref,
                blocking=record.blocking,
            )
            for record in unsupported_meta_clauses
        )
        lowered_temporal_events = tuple(
            lower_effect_intents_to_temporal_events(
                lowered_effect_intents,
                source_ref=source_ref,
                source_title=source_title,
                source_issue_date=amendment_issue_date,
                source_effective_date=amendment_effective_date,
                group_id_prefix=f"finland-johto:{source_ref or 'unknown'}",
                target_statute=target_statute,
            )
        )

        meta_clauses = extract_meta_surface_clauses(johto)
        activation_lowering = activation_rules_from_meta_clauses_with_findings(meta_clauses)
        all_findings.extend(activation_lowering.findings)
        activation_rules = list(activation_lowering.activation_rules)
        if not activation_rules:
            activation_rules = [default_activation_rule()]

        if compiled_ops_out is not None and activation_rules:
            rule = activation_rules[0]
            for cop_dict in compiled_ops_out:
                if "activation_rule" not in cop_dict:
                    cop_dict["activation_rule"] = {
                        "kind": rule.kind,
                        "effective_date": rule.effective_date,
                        "condition_ref": rule.condition_ref,
                    }
                    cop_dict["is_contingent"] = classify_contingent(rule)

    # WAIST #6 single-channel (ESCALATE-5D): route the canonical-op decline
    # THROUGH the typed StageResult.residuals. The blocking findings
    # (obligations/violations) are projected onto blocking Residuals (each
    # carrying its exact Finding); the returned decline is then RECONSTRUCTED by
    # reading the carried Finding back out of every blocking residual. The typed
    # residual is therefore the SOLE source of the returned decline — stripping
    # the residual list removes the decline (the fire-drill bite). Observation
    # findings are not part of the decline and flow through unchanged. On the
    # green corpus the blocking-finding set is reconstructed verbatim from its own
    # residuals → byte-identical findings → 0-delta.
    stage, carriers = build_canonical_op_stage(resolved, all_findings)
    if canonical_op_stage_out is not None:
        # WAIST #6 carrier: observe the per-amendment canonical-op StageResult so
        # the replay can aggregate it onto ReplayProducts.canonical_op_stage. This
        # is the EXACT account that backs the decline single-channel below — a
        # faithful capture, not a union-findings re-derivation.
        canonical_op_stage_out.append(stage)
    returned_findings = reconstruct_findings_from_canonical_op_stage(
        all_findings, stage, carriers
    )
    return PhaseResult(
        output=resolved,
        findings=returned_findings,
        temporal_events=lowered_temporal_events,
    )


def reconstruct_findings_from_canonical_op_stage(
    all_findings: list[Finding],
    stage: StageResult[list[ResolvedOp]],
    carriers: tuple[_CanonicalOpResidualCarrier, ...],
) -> tuple[Finding, ...]:
    """Rebuild the returned findings so the decline rides the typed residual.

    Observation findings pass through in their original order. Every blocking
    finding (the decline) is emitted ONLY when its corresponding typed blocking
    residual is present in ``stage.residuals`` — so the residual list is the
    single source of the returned decline. Insertion order is preserved
    (byte-identical on the green corpus → 0-delta). A ``stage`` whose residuals
    have been stripped (the fire-drill double) yields no blocking findings (the
    decline disappears).
    """
    live_residual_ids = {id(residual) for residual in stage.residuals}
    finding_to_residual_id = {
        id(carrier.finding): id(carrier.residual) for carrier in carriers
    }
    out: list[Finding] = []
    for finding in all_findings:
        residual_id = finding_to_residual_id.get(id(finding))
        if residual_id is None:
            # observation / non-decline finding — passes through untouched.
            out.append(finding)
            continue
        # blocking decline finding — emit ONLY if its typed residual is live.
        if residual_id in live_residual_ids:
            out.append(finding)
    return tuple(out)

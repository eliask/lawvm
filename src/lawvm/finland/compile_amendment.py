"""Amendment-level compilation for Finland replay."""

from __future__ import annotations

from typing import Any, Literal, Optional, cast

from lawvm.core.compile_result import StrictProfile
from lawvm.core.effect_lowering import lower_effect_intents_to_temporal_events
from lawvm.core.elaboration_context import TargetUnitKind, snapshot_replay_lookups
from lawvm.core.phase_result import Finding, PhaseResult
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
) -> PhaseResult[list[ResolvedOp]]:
    """Compile grouped amendment ops into resolved ops ready for application."""
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

    return PhaseResult(
        output=resolved,
        findings=tuple(all_findings),
        temporal_events=lowered_temporal_events,
    )

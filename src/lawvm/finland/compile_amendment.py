"""Amendment-level compilation for Finland replay."""

from __future__ import annotations

from typing import Any, Literal, Optional, cast

import lxml.etree as etree

from lawvm.core.compile_result import ActivationRule, StrictProfile, TemporalEvent
from lawvm.core.effect_lowering import lower_effect_intents_to_temporal_events
from lawvm.core.elaboration_context import TargetUnitKind, snapshot_replay_lookups
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.finland.compile_group import compile_group_typed as _compile_group_typed
from lawvm.finland.compile_group_boundary import CompileGroupRequest, CompileGroupSinks
from lawvm.finland.effect_lowering import UnsupportedMetaClause, lower_johto_effects
from lawvm.finland.frontend_compile import _tree_title
from lawvm.finland.group_plan import (
    coalesce_same_target_mixed_scope_section_groups,
    group_ops_by_target,
)
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses
from lawvm.finland.metadata import _amendment_effective_date, _statute_issue_date
from lawvm.finland.ops import AmendmentOp, ResolvedOp, get_replay_profile
from lawvm.finland.scope import find_body_section_chapter
from lawvm.finland.standalone_targets import (
    group_shadow_pruning_foreign_scoped_section_targets,
    group_shadow_pruning_section_targets,
)
from lawvm.finland.statute import ReplayState
from lawvm.finland.temporal_lowering import (
    activation_rules_from_meta_clauses_with_findings,
    classify_contingent,
    default_activation_rule,
)


def compile_amendment_ops(
    master: ReplayState,
    ops: list[AmendmentOp],
    muutos_tree: etree._Element,
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
    source_title = source_title or _tree_title(muutos_tree)
    amendment_issue_date = _statute_issue_date(muutos_tree)
    amendment_effective_date = _amendment_effective_date(muutos_tree)
    section_groups = coalesce_same_target_mixed_scope_section_groups(
        group_ops_by_target(ops),
        master=master,
        find_body_section_chapter=lambda target_norm: find_body_section_chapter(muutos_tree, target_norm),
    )
    inserted_chapter_labels = {
        _norm_num_token(op.target_section or "")
        for op in ops
        if op.target_unit_kind == "chapter" and op.op_type == "INSERT" and op.target_section
    }
    resolved: list[ResolvedOp] = []
    all_findings: list[Finding] = []

    precomputed_lookups = snapshot_replay_lookups(cast(Any, master))

    for group_key, group_ops in section_groups.items():
        target_unit_kind_value = cast(TargetUnitKind, group_key.unit_kind.value)
        standalone_section_targets = group_shadow_pruning_section_targets(
            ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=group_key.target_norm,
            target_part=group_key.target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        foreign_scoped_standalone_section_targets = group_shadow_pruning_foreign_scoped_section_targets(
            ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=group_key.target_norm,
            target_part=group_key.target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        group_result = _compile_group_typed(
            CompileGroupRequest(
                master=master,
                target_unit_kind=target_unit_kind_value,
                target_norm=group_key.target_norm,
                target_chapter=group_key.target_chapter,
                target_part=group_key.target_part,
                group_ops=group_ops,
                standalone_section_targets=standalone_section_targets,
                foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
                inserted_chapter_labels=inserted_chapter_labels,
                muutos_tree=muutos_tree,
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

"""Compile one Finland amendment target group through staged contracts."""

from __future__ import annotations

from typing import Any, cast

from lawvm.core.elaboration_context import snapshot_replay_lookups, snapshot_target_context
from lawvm.core.phase_result import PhaseResult
from lawvm.finland.compile_group_boundary import CompileGroupRequest, CompileGroupSinks
from lawvm.finland.compile_group_elaboration import ElaborateGroupRequest, elaborate_group
from lawvm.finland.compile_group_lowering import LowerGroupRequest, LowerGroupSinks, lower_group
from lawvm.finland.compile_group_scope_recovery import (
    CompileGroupScopeRecoveryRequest,
    resolve_compile_group_scope_recovery,
)
from lawvm.finland.compile_group_surface import BuildGroupSurfaceRequest, build_group_surface
from lawvm.finland.ops import ResolvedOp


def compile_group_typed(
    request: CompileGroupRequest,
    sinks: CompileGroupSinks | None = None,
) -> PhaseResult[list[ResolvedOp]]:
    """Compile one same-target group of amendment ops into ResolvedOps."""
    sinks = sinks or CompileGroupSinks()
    master = request.master
    target_unit_kind = request.target_unit_kind
    target_norm = request.target_norm
    target_chapter = request.target_chapter
    target_part = request.target_part
    group_ops = request.group_ops
    standalone_section_targets = request.standalone_section_targets
    inserted_chapter_labels = request.inserted_chapter_labels
    source_model = request.source_model
    johto = request.johto
    profile = request.profile
    strict_profile = request.strict_profile
    foreign_scoped_standalone_section_targets = set(
        request.foreign_scoped_standalone_section_targets
    )
    foreign_scoped_replace_section_targets = set(
        request.foreign_scoped_replace_section_targets
    )
    foreign_scoped_descendant_section_targets = set(
        request.foreign_scoped_descendant_section_targets
    )
    foreign_scoped_replace_section_target_scopes = frozenset(
        request.foreign_scoped_replace_section_target_scopes
    )
    sparse_omission_tail_claims = request.sparse_omission_tail_claims
    compiled_ops_out = sinks.compiled_ops_out

    recovery_result = resolve_compile_group_scope_recovery(
        CompileGroupScopeRecoveryRequest(
            master=master,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            group_ops=group_ops,
            inserted_chapter_labels=inserted_chapter_labels,
            source_model=source_model,
            johto=johto,
            strict_profile=strict_profile,
            amendment_group_ops=request.amendment_group_ops,
        )
    )
    recovery = recovery_result.output
    compile_findings = recovery_result.findings()
    if recovery.blocked:
        return PhaseResult(output=[], findings=compile_findings)

    lookups = request.lookups if request.lookups is not None else snapshot_replay_lookups(cast(Any, master))
    target_ctx = snapshot_target_context(
        cast(Any, master),
        target_unit_kind,
        target_norm,
        recovery.effective_target_chapter,
        lookups,
        target_part=recovery.effective_target_part,
    )

    surface_result = build_group_surface(
        BuildGroupSurfaceRequest(
            group_ops=recovery.group_ops,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=recovery.surface_target_chapter,
            target_part=recovery.surface_target_part,
            source_model=source_model,
            sparse_omission_tail_claims=sparse_omission_tail_claims,
            amendment_group_ops=request.amendment_group_ops,
        )
    )

    elab_result = elaborate_group(
        ElaborateGroupRequest(
            target_ctx=target_ctx,
            lookups=lookups,
            group_surface=surface_result.output,
            group_ops=recovery.group_ops,
            standalone_section_targets=standalone_section_targets,
            foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
            foreign_scoped_descendant_section_targets=foreign_scoped_descendant_section_targets,
            foreign_scoped_replace_section_targets=foreign_scoped_replace_section_targets,
            foreign_scoped_replace_section_target_scopes=foreign_scoped_replace_section_target_scopes,
            effective_target_part=recovery.effective_target_part,
            source_model=source_model,
            johto=johto,
            profile=profile,
            strict_profile=strict_profile,
            sparse_omission_tail_claims=sparse_omission_tail_claims,
        )
    )
    elaborated = elab_result.output
    if elaborated.was_filtered or not elaborated.group_ops:
        return PhaseResult(
            output=[],
            findings=surface_result.findings() + elab_result.findings() + compile_findings,
        )

    lower_result = lower_group(
        LowerGroupRequest(
            target_ctx=target_ctx,
            elaborated=elaborated,
            master=master,
            lookups=lookups,
        ),
        LowerGroupSinks(compiled_ops_out=compiled_ops_out),
    )

    return PhaseResult(
        output=lower_result.output,
        findings=surface_result.findings() + elab_result.findings() + lower_result.findings() + compile_findings,
    )


_compile_group_typed = compile_group_typed

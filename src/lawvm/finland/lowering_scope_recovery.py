"""Scope recovery helpers for Finland group lowering."""

from __future__ import annotations

from collections.abc import Iterable

from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.finland.ops import AmendmentOp, projection_scope_confidence
from lawvm.finland.source_model import AmendmentSourceModel


def group_has_scope_source(group_ops: Iterable[AmendmentOp], source: str) -> bool:
    source_norm = str(source or "").strip()
    if not source_norm:
        return False
    return any(
        (
            (
                witness := projection_scope_confidence(
                    scope_confidence=op.scope_confidence,
                    scope_provenance_tags=op.scope_provenance_tags,
                    resolved_chapter=op.target_cols.target_chapter,
                )
            )
            is not None
            and witness.source == source_norm
        )
        for op in group_ops
    )


def allow_unscoped_live_section_retarget(
    group_ops: Iterable[AmendmentOp],
) -> str | None:
    if group_has_scope_source(group_ops, "carry_forward"):
        return "carry_forward"
    if group_has_scope_source(group_ops, "explicit_scope_rewrite"):
        return "explicit_scope_rewrite"
    if group_has_scope_source(group_ops, "explicit_chunk"):
        return "explicit_chunk"
    return None


def resolve_group_surface_scope(
    *,
    source_model: AmendmentSourceModel,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
    group_ops: Iterable[AmendmentOp],
) -> tuple[str | None, str | None]:
    """Return the Stage-1 payload-extraction scope for one target group.

    This is intentionally source-facing. It may differ from the live/effective
    target scope when the amendment body still carries the section payload under
    an earlier chapter wrapper even though the lowering path has already been
    retargeted to the current live chapter.
    """
    surface_target_chapter = target_chapter
    surface_target_part = target_part
    carry_forward_scoped = group_has_scope_source(group_ops, "carry_forward")

    if target_unit_kind != "section":
        return surface_target_chapter, surface_target_part

    body_scope = source_model.body_section_scope(target_norm)
    if carry_forward_scoped and body_scope == (None, None):
        return None, None
    if target_chapter and body_scope is not None:
        body_part, body_chapter = body_scope
        scoped_node_exists = source_model.body_has_section(
            target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
        )
        body_node_exists = source_model.body_has_section(
            target_norm,
            target_chapter=body_chapter,
            target_part=body_part,
        )
        if (
            not scoped_node_exists
            and body_node_exists
            and (body_chapter != target_chapter or body_part != target_part)
        ):
            return body_chapter, body_part

    return surface_target_chapter, surface_target_part

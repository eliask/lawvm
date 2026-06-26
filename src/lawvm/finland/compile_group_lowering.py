"""Stage 3 lowering for Finland compile groups."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, cast

from lawvm.core.elaboration_context import ReplayLookups, TargetContext, snapshot_target_context
from lawvm.core.ir import LegalAddress
from lawvm.core.phase_result import PhaseBuilder, PhaseResult
from lawvm.finland.constraints import DEBUG
from lawvm.finland.elaborated_group import ElaboratedGroup
from lawvm.finland.group_ops import (
    append_compiled_group_ops,
    sort_group_ops_for_apply,
    stabilize_insert_order,
)
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import ResolvedOp
from lawvm.finland.statute import ReplayState


def assert_intent_agrees_with_legacy(rop: ResolvedOp) -> None:
    """DEBUG-only: verify typed intent is consistent with legacy waist fields."""
    from lawvm.core.canonical_intent import (
        FacetTarget,
        Insert,
        IntentKind,
        NodeTarget,
        Repeal,
        Replace,
    )

    intent = rop.intent
    assert intent is not None

    kind_to_op_type = {
        IntentKind.REPLACE: "REPLACE",
        IntentKind.INSERT: "INSERT",
        IntentKind.REPEAL: "REPEAL",
        IntentKind.RELABEL: "RENUMBER",
    }
    if intent.kind in kind_to_op_type:
        assert rop.resolved_action_type == kind_to_op_type[intent.kind], (
            f"Intent kind {intent.kind} disagrees with op_type {rop.resolved_action_type} for {rop.op_id}"
        )

    if isinstance(intent, (Replace, Repeal)):
        target = intent.target
        if isinstance(target, FacetTarget):
            target_special = rop.effective_target_special
            if target.facet == "heading":
                assert target_special in ("otsikko", "otsikko_edella"), (
                    f"FacetTarget(heading) but target_special={target_special} for {rop.op_id}"
                )
            elif target.facet == "intro":
                assert target_special == "johd", (
                    f"FacetTarget(intro) but target_special={target_special} for {rop.op_id}"
                )
        elif isinstance(target, NodeTarget):
            assert rop.effective_target_special is None, (
                f"NodeTarget but target_special={rop.effective_target_special} for {rop.op_id}"
            )

    if isinstance(intent, Insert):
        assert intent.contract.insert_order is not None, f"Insert intent missing insert_order for {rop.op_id}"


@dataclass(frozen=True, slots=True)
class LowerGroupRequest:
    """Typed inputs for lowering elaborated group ops to ResolvedOps."""

    target_ctx: TargetContext
    elaborated: ElaboratedGroup
    master: Optional[ReplayState] = None
    lookups: Optional[ReplayLookups] = None


@dataclass(frozen=True, slots=True)
class LowerGroupSinks:
    """Mutable evidence/output channels for compile-group lowering."""

    compiled_ops_out: Optional[list[dict[str, object]]] = None


def lower_group(
    request: LowerGroupRequest,
    sinks: Optional[LowerGroupSinks] = None,
) -> PhaseResult[list[ResolvedOp]]:
    """Stage 3: lower elaborated ops to ResolvedOps. Pure of live state."""
    target_ctx = request.target_ctx
    elaborated = request.elaborated
    master = request.master
    lookups = request.lookups
    compiled_ops_out = sinks.compiled_ops_out if sinks is not None else None

    target_chapter = target_ctx.target_chapter
    group_ops = list(elaborated.group_ops)
    muutos_ir = elaborated.muutos_ir
    cross_ir = elaborated.cross_ir
    remapped_target_norm = elaborated.remapped_target_norm
    slot_assignment = elaborated.slot_assignment

    if remapped_target_norm != target_ctx.target_norm and master is not None and lookups is not None:
        sort_ctx = snapshot_target_context(
            cast(Any, master),
            target_ctx.target_unit_kind,
            remapped_target_norm,
            target_chapter,
            lookups,
        )
    else:
        sort_ctx = target_ctx

    sorted_ops = sort_group_ops_for_apply(sort_ctx, group_ops)
    sorted_ops = stabilize_insert_order(sorted_ops, sort_ctx)

    resolved: list[ResolvedOp] = []
    for op in sorted_ops:
        resolved_target_chapter = op.target_cols.target_chapter if op.target_cols.target_chapter is not None else target_chapter
        target_address = op.lo.target if op.lo is not None else None
        destination_address = (op.lo.destination if op.lo is not None else None) or (
            op.lo.anchor if op.lo is not None else None
        )
        if (
            op.target_version_statute_id
            and op.lo is not None
            and op.target_cols.target_unit_kind == "section"
            and op.target_cols.target_section
            and tuple(op.lo.target.path) == (("section", op.target_cols.target_section),)
            and master is not None
            and _norm_num_token(op.target_cols.target_section) not in master.duplicate_section_labels
        ):
            cited_live_path = master.find_section_path(op.target_cols.target_section, None, op.target_cols.target_part)
            if cited_live_path is not None and any(kind in {"chapter", "part"} for kind, _label in cited_live_path):
                target_address = LegalAddress(path=tuple(cited_live_path))
        if (
            op.move_clause_target_unit_kind is not None
            and op.lo is not None
            and op.target_cols.target_unit_kind == "section"
            and muutos_ir is None
        ):
            source_path = master.find_section_path(op.target_cols.target_section, None, op.target_cols.target_part) if master is not None else None
            if source_path is not None:
                target_address = LegalAddress(path=tuple(source_path))
            if target_address is not None:
                destination_address = op.lo.target
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=muutos_ir,
            cross_ir=cross_ir,
            target_unit_kind=target_ctx.target_unit_kind,
            target_norm=remapped_target_norm,
            target_chapter=resolved_target_chapter,
            slot_assignment=slot_assignment,
            payload_completeness=elaborated.payload_completeness,
            target_address=target_address,
            destination_address=destination_address,
        )
        if DEBUG and rop.intent is not None:
            assert_intent_agrees_with_legacy(rop)
        resolved.append(rop)
    append_compiled_group_ops(compiled_ops_out, resolved)
    return PhaseBuilder().finish(resolved)


_assert_intent_agrees_with_legacy = assert_intent_agrees_with_legacy
_LowerGroupRequest = LowerGroupRequest
_LowerGroupSinks = LowerGroupSinks
_lower_group = lower_group

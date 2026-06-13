"""Replay-history LegalOperation emitters for Finland replay."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.apply_ir_ops import _relabel_subsection_ir
from lawvm.finland.apply_runtime_support import _snapshot_op_source, _valid_target_group_path_hint
from lawvm.finland.ops import ResolvedOp

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState


def emit_granular_subsection_timeline_ops(
    state: "ReplayState",
    group_rops: list[ResolvedOp],
    lo_ops_out: list[_LegalOperation],
    amendment_id: str,
    source_title: str,
    amendment_issue_date: dt.date | None,
    amendment_effective_date: dt.date | None,
    base_ir: IRNode | None,
    path_hint: tuple[tuple[str, str], ...] | None = None,
) -> bool:
    """Emit subsection-addressed timeline ops for eligible pure moment-level groups.

    This only fires when the normal section-snapshot export would otherwise
    inherit a live temporary section expiry from an earlier snapshot. Without
    that guard, subsection-only export can lose older stable sibling moments
    that currently still depend on section snapshots.
    """
    if not group_rops:
        return False
    if len(group_rops) != 1:
        return False

    first = group_rops[0]
    first_group = first.resolved_group_key_view
    if first_group.unit_kind != "section":
        return False
    if base_ir is None or _tops.find(base_ir, "section", first_group.target_norm) is None:
        return False

    for rop in group_rops:
        if (
            rop.resolved_group_key_view.unit_kind != "section"
            or not rop.targets_subsection_only()
            or not rop.is_replace_action
        ):
            return False
        if not rop.has_assigned_subsection_payload():
            return False

    op_source = _snapshot_op_source(
        group_rops,
        amendment_id,
        source_title,
        amendment_issue_date,
        amendment_effective_date,
    )
    sec_path = _valid_target_group_path_hint(
        state,
        first_group.unit_kind,
        first_group.target_norm,
        first_group.target_chapter,
        first_group.target_part,
        path_hint,
    )
    if sec_path is None:
        sec_path = state.find_section_path(
            first_group.target_norm,
            first_group.target_chapter,
            first_group.target_part,
        )
    if sec_path is None:
        return False

    tl_sec_path = tuple((k, v) for k, v in sec_path if v)
    if not tl_sec_path:
        return False
    if op_source.expires:
        return False

    effective_iso = amendment_effective_date.isoformat() if amendment_effective_date else ""
    prior_section_version = None
    for lo in reversed(lo_ops_out):
        if lo.target.path == tl_sec_path:
            prior_section_version = lo
            break
    if prior_section_version is None:
        return False
    prior_expires = (prior_section_version.source.expires if prior_section_version.source else "") or ""
    if not prior_expires or (effective_iso and prior_expires <= effective_iso):
        return False

    for seq, rop in enumerate(group_rops, start=1):
        amend_sub = rop.resolved_amend_sub_ir()
        assert amend_sub is not None
        target_subsection_label = rop.resolved_target_subsection_label
        assert target_subsection_label is not None
        target_label = str(target_subsection_label)
        payload = amend_sub if amend_sub.label == target_label else _relabel_subsection_ir(amend_sub, target_label)

        lo_ops_out.append(
            _LegalOperation(
                op_id=f"subsection_{amendment_id}_{first_group.target_norm}_{target_label}_{seq}",
                sequence=seq,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=tl_sec_path + (("subsection", target_label),)),
                payload=payload,
                source=op_source,
                group_id=f"finland-johto:{amendment_id}",
            )
        )
    return True

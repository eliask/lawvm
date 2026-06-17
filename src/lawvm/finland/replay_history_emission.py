"""Replay-history LegalOperation emitters for Finland replay."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import TYPE_CHECKING

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.source_pathology import build_temporary_section_rebase_pathology
from lawvm.finland.apply_ir_ops import _relabel_subsection_ir
from lawvm.finland.apply_runtime_support import _snapshot_op_source, _valid_target_group_path_hint
from lawvm.finland.ops import ResolvedOp

if TYPE_CHECKING:
    from lawvm.core.compile_result import SourcePathology
    from lawvm.finland.statute import ReplayState


def _granular_subsection_target_label(rop: ResolvedOp) -> str:
    label = str(rop.resolved_target_subsection_label or "").strip()
    if label:
        return label
    paragraph = rop.effective_target_paragraph
    return str(paragraph) if paragraph is not None else ""


def _granular_targets_subsection_only(rop: ResolvedOp) -> bool:
    address = rop.resolved_target_address
    if address is not None and address.path and address.path[-1][0] == "subsection":
        return (
            address.special is None
            and rop.effective_target_item_label is None
            and rop.effective_target_special is None
        )
    if rop.effective_target_paragraph is None:
        return False
    if rop.effective_target_item_label is not None or rop.effective_target_special is not None:
        return False
    if address is not None and address.path:
        return address.special is None and address.path[-1][0] == "section"
    return True


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
    source_pathologies_out: list["SourcePathology"] | None = None,
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
            or not _granular_targets_subsection_only(rop)
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

    prior_payload = prior_section_version.payload
    if prior_payload is None or prior_payload.kind.value != "section":
        return False

    replacement_payloads: list[tuple[str, IRNode]] = []
    for seq, rop in enumerate(group_rops, start=1):
        amend_sub = rop.resolved_amend_sub_ir()
        assert amend_sub is not None
        target_label = _granular_subsection_target_label(rop)
        assert target_label
        payload = amend_sub if amend_sub.label == target_label else _relabel_subsection_ir(amend_sub, target_label)
        replacement_payloads.append((target_label, payload))

    bridge_children: list[IRNode] = []
    remaining = {target_label: payload for target_label, payload in replacement_payloads}
    for child in prior_payload.children:
        if child.kind.value == "subsection" and child.label in remaining:
            bridge_children.append(remaining.pop(child.label))
            continue
        bridge_children.append(child)
    if remaining:
        return False

    bridge_payload = IRNode(
        kind=prior_payload.kind,
        label=prior_payload.label,
        text=prior_payload.text,
        attrs=dict(prior_payload.attrs),
        children=tuple(bridge_children),
    )
    bridge_source = replace(op_source, expires=prior_expires)
    lo_ops_out.append(
        _LegalOperation(
            op_id=f"snapshot_section_{first_group.target_norm}_temporary_parent_bridge_{amendment_id}",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=tl_sec_path),
            payload=bridge_payload,
            source=bridge_source,
            group_id=f"finland-johto:{amendment_id}",
        )
    )
    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_temporary_section_rebase_pathology(
                source_statute=op_source.statute_id,
                target_section=first_group.target_norm,
                target_chapter=first_group.target_chapter or "",
                rebase_context="granular_subsection_timeline_parent_bridge",
                rebase_kind="active_temporary_parent_bridge",
                latest_snapshot_expires=prior_expires,
            )
        )

    for seq, (target_label, payload) in enumerate(replacement_payloads, start=1):
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

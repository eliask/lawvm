"""Finland adapter for the generic post-fold payload realization audit."""

from __future__ import annotations

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_realization import (
    PayloadRealizationUnit,
    audit_payload_realization,
    payload_realization_gap_findings,
)
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ops import ResolvedOp

_REALIZING_ACTION_TYPES = frozenset({"INSERT", "REPLACE"})


def payload_realization_findings(
    *,
    resolved_ops: tuple[ResolvedOp, ...],
    after_ir: IRNode,
    amendment_id: str,
) -> tuple[Finding, ...]:
    """Return audit findings for claimed operation payload absent from ``after_ir``.

    The comparison is intentionally text-realization only.  A failure here says
    "a resolved operation's payload text did not survive the fold"; it does not
    infer a target address, change action family, or mutate replay output.
    """

    units = _payload_realization_units(resolved_ops)
    gaps = audit_payload_realization(
        units=units,
        after_text=irnode_to_text(after_ir),
    )
    return payload_realization_gap_findings(gaps, source_ref=amendment_id)


def _payload_realization_units(
    resolved_ops: tuple[ResolvedOp, ...],
) -> tuple[PayloadRealizationUnit, ...]:
    units: list[PayloadRealizationUnit] = []
    for index, rop in enumerate(resolved_ops):
        if rop.resolved_action_type not in _REALIZING_ACTION_TYPES:
            continue
        payload_ir = rop.resolved_amend_sub_ir() or rop.muutos_ir or rop.cross_ir
        if payload_ir is None:
            continue
        unit_id = rop.op_id or f"resolved_op_{index}"
        target = rop.resolved_target_address
        units.append(
            PayloadRealizationUnit(
                unit_id=unit_id,
                unit_kind=rop.resolved_action_type,
                observed_label=rop.resolved_target_label,
                parent_label=str(target or ""),
                text_chunks=_payload_text_chunks(payload_ir),
            )
        )
    return tuple(units)


def _payload_text_chunks(node: IRNode) -> tuple[str, ...]:
    chunks: list[str] = []
    _collect_chunks(node, chunks)
    return tuple(dict.fromkeys(chunks))


def _collect_chunks(node: IRNode, chunks: list[str]) -> None:
    if node.kind is IRNodeKind.OMISSION:
        return
    if node.text:
        chunks.append(node.text)
    for child in node.children:
        _collect_chunks(child, chunks)


__all__ = [
    "payload_realization_findings",
]

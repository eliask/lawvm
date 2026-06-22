"""Finland adapter for the generic post-fold payload realization audit."""

from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress
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
        action_type = getattr(rop, "resolved_action_type", "")
        if action_type not in _REALIZING_ACTION_TYPES:
            continue
        payload_ir = rop.resolved_amend_sub_ir() or rop.muutos_ir or rop.cross_ir
        if payload_ir is None:
            continue
        payload_ir = _target_scoped_payload_ir(payload_ir, rop.resolved_target_address)
        unit_id = rop.op_id or f"resolved_op_{index}"
        target = rop.resolved_target_address
        units.append(
            PayloadRealizationUnit(
                unit_id=unit_id,
                unit_kind=action_type,
                observed_label=rop.resolved_target_label,
                parent_label=str(target or ""),
                text_chunks=_payload_text_chunks(payload_ir),
            )
        )
    return tuple(units)


def _target_scoped_payload_ir(payload_ir: IRNode, target: LegalAddress | None) -> IRNode:
    """Return the payload subtree owned by ``target`` when structurally provable."""

    if target is None or not target.path:
        return payload_ir
    terminal_kind, terminal_label = target.path[-1]
    if terminal_kind not in _TARGET_NODE_KINDS:
        return payload_ir
    if _node_matches_target(payload_ir, terminal_kind, terminal_label):
        return payload_ir
    matching_descendants = tuple(
        node
        for node in _walk_ir(payload_ir)
        if node is not payload_ir and _node_matches_target(node, terminal_kind, terminal_label)
    )
    if len(matching_descendants) == 1:
        return matching_descendants[0]
    return payload_ir


def _walk_ir(node: IRNode) -> tuple[IRNode, ...]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_walk_ir(child))
    return tuple(nodes)


_TARGET_NODE_KINDS: dict[str, frozenset[IRNodeKind]] = {
    "part": frozenset({IRNodeKind.PART}),
    "chapter": frozenset({IRNodeKind.CHAPTER}),
    "section": frozenset({IRNodeKind.SECTION}),
    "subsection": frozenset({IRNodeKind.SUBSECTION}),
    "item": frozenset({IRNodeKind.ITEM, IRNodeKind.PARAGRAPH}),
    "subitem": frozenset({IRNodeKind.SUBPARAGRAPH}),
}


def _node_matches_target(node: IRNode, target_kind: str, target_label: str) -> bool:
    return node.kind in _TARGET_NODE_KINDS[target_kind] and node.label == target_label


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

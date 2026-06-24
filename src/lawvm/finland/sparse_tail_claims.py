"""Sparse omission-tail payload claims for Finland amendment bodies."""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import AmendmentOp, OpType
from lawvm.finland.source_model import AmendmentSourceModel


SPARSE_OMISSION_TAIL_CLAIM_RULE = "ELAB.SPARSE_OMISSION_TAIL_CLAIM"
SPARSE_OMISSION_TAIL_PRUNE_RULE = "ELAB.SPARSE_OMISSION_TAIL_PRUNED_FROM_CARRIER"


@dataclass(frozen=True, slots=True)
class SparseOmissionTailClaim:
    """A descendant op claims a post-omission subsection carried by another section."""

    source_statute: str
    carrier_section: str
    carrier_target_chapter: str | None
    carrier_target_part: str | None
    carrier_source_chapter: str | None
    carrier_source_part: str | None
    target_section: str
    target_chapter: str | None
    target_part: str | None
    target_paragraph: int
    payload_slot_label: str
    payload_slot_index: int
    payload_subsection: IRNode

    def payload_section_ir(self) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SECTION,
            label=self.target_section,
            children=(
                IRNode(kind=IRNodeKind.NUM, text=f"{self.target_section} §"),
                self.payload_subsection,
            ),
        )

    def detail(self) -> dict[str, object]:
        return {
            "source_statute": self.source_statute,
            "carrier_section": self.carrier_section,
            "carrier_target_chapter": self.carrier_target_chapter or "",
            "carrier_source_chapter": self.carrier_source_chapter or "",
            "target_section": self.target_section,
            "target_chapter": self.target_chapter or "",
            "target_paragraph": self.target_paragraph,
            "payload_slot_label": self.payload_slot_label,
            "payload_slot_index": self.payload_slot_index,
            "rule": SPARSE_OMISSION_TAIL_CLAIM_RULE,
        }


def _norm_opt(value: str | None) -> str | None:
    return _norm_num_token(value or "") if value else None


def _subsection_label_key(value: str | None) -> str:
    token = _norm_num_token(value or "")
    digits: list[str] = []
    for char in token:
        if char.isdigit():
            digits.append(char)
            continue
        if digits:
            break
    return "".join(digits)


def _matches_target(
    claim: SparseOmissionTailClaim,
    *,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
) -> bool:
    return (
        claim.target_section == _norm_num_token(target_norm)
        and claim.target_chapter == _norm_opt(target_chapter)
        and claim.target_part == _norm_opt(target_part)
    )


def sparse_tail_claim_for_target(
    claims: tuple[SparseOmissionTailClaim, ...],
    *,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
) -> SparseOmissionTailClaim | None:
    matches = tuple(
        claim
        for claim in claims
        if _matches_target(
            claim,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
        )
    )
    return matches[0] if len(matches) == 1 else None


def build_sparse_omission_tail_claims(
    ops: list[AmendmentOp],
    source_model: AmendmentSourceModel,
) -> tuple[SparseOmissionTailClaim, ...]:
    """Find unique source-body omission tails claimed by explicit descendant ops."""
    descendant_ops = [
        op
        for op in ops
        if op.op_type in {OpType.REPLACE, OpType.INSERT}
        and op.target_unit_kind == "section"
        and op.target_paragraph is not None
        and not op.target_item
        and not op.target_special
        and source_model.lookup_payload_ir("section", op.target_section, None, None).status
        == "missing"
    ]
    carrier_ops = [
        op
        for op in ops
        if op.op_type == OpType.REPLACE
        and op.target_unit_kind == "section"
        and op.target_paragraph is None
        and not op.target_item
        and not op.target_special
    ]

    candidates: list[SparseOmissionTailClaim] = []
    for carrier_op in carrier_ops:
        carrier_payload = source_model.lookup_payload_ir(
            "section",
            carrier_op.target_section,
            None,
            None,
        )
        carrier_ir = carrier_payload.payload_ir
        if carrier_ir is None or carrier_ir.kind is not IRNodeKind.SECTION:
            continue
        saw_omission = False
        tail_slots: list[tuple[int, IRNode]] = []
        for idx, child in enumerate(carrier_ir.children):
            if child.kind is IRNodeKind.OMISSION:
                saw_omission = True
                continue
            if saw_omission and child.kind is IRNodeKind.SUBSECTION:
                tail_slots.append((idx, child))
        if not tail_slots:
            continue

        body_scope = source_model.body_section_scope(carrier_op.target_section)
        carrier_source_part, carrier_source_chapter = body_scope if body_scope is not None else (None, None)
        for desc_op in descendant_ops:
            if desc_op is carrier_op or desc_op.target_paragraph is None:
                continue
            matching_slots = [
                (idx, slot)
                for idx, slot in tail_slots
                if _subsection_label_key(slot.label) == str(desc_op.target_paragraph)
            ]
            if len(matching_slots) != 1:
                continue
            slot_idx, slot = matching_slots[0]
            candidates.append(
                SparseOmissionTailClaim(
                    source_statute=str(desc_op.source_statute or carrier_op.source_statute or ""),
                    carrier_section=_norm_num_token(carrier_op.target_section),
                    carrier_target_chapter=_norm_opt(carrier_op.target_chapter),
                    carrier_target_part=_norm_opt(carrier_op.target_part),
                    carrier_source_chapter=carrier_source_chapter,
                    carrier_source_part=carrier_source_part,
                    target_section=_norm_num_token(desc_op.target_section),
                    target_chapter=_norm_opt(desc_op.target_chapter),
                    target_part=_norm_opt(desc_op.target_part),
                    target_paragraph=desc_op.target_paragraph,
                    payload_slot_label=str(slot.label or ""),
                    payload_slot_index=slot_idx,
                    payload_subsection=slot,
                )
            )

    target_counts: dict[tuple[str, str | None, str | None, int], int] = {}
    carrier_counts: dict[tuple[str, str | None, str | None, int], int] = {}
    for claim in candidates:
        target_key = (
            claim.target_section,
            claim.target_chapter,
            claim.target_part,
            claim.target_paragraph,
        )
        carrier_key = (
            claim.carrier_section,
            claim.carrier_target_chapter,
            claim.carrier_target_part,
            claim.payload_slot_index,
        )
        target_counts[target_key] = target_counts.get(target_key, 0) + 1
        carrier_counts[carrier_key] = carrier_counts.get(carrier_key, 0) + 1

    return tuple(
        claim
        for claim in candidates
        if target_counts[
            (
                claim.target_section,
                claim.target_chapter,
                claim.target_part,
                claim.target_paragraph,
            )
        ]
        == 1
        and carrier_counts[
            (
                claim.carrier_section,
                claim.carrier_target_chapter,
                claim.carrier_target_part,
                claim.payload_slot_index,
            )
        ]
        == 1
    )


def prune_sparse_tail_claims_from_carrier(
    muutos_ir: IRNode | None,
    claims: tuple[SparseOmissionTailClaim, ...],
    *,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
) -> tuple[IRNode | None, tuple[SparseOmissionTailClaim, ...]]:
    """Remove tail slots from the carrier payload after another op has claimed them."""
    if muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return muutos_ir, ()
    normalized_target_chapter = _norm_opt(target_chapter)
    normalized_target_part = _norm_opt(target_part)
    matching_claims = tuple(
        claim
        for claim in claims
        if claim.carrier_section == _norm_num_token(target_norm)
        and normalized_target_chapter in {
            claim.carrier_target_chapter,
            claim.carrier_source_chapter,
        }
        and normalized_target_part in {
            claim.carrier_target_part,
            claim.carrier_source_part,
        }
    )
    if not matching_claims:
        return muutos_ir, ()

    claim_by_index = {claim.payload_slot_index: claim for claim in matching_claims}
    new_children: list[IRNode] = []
    pruned: list[SparseOmissionTailClaim] = []
    saw_omission = False
    for idx, child in enumerate(muutos_ir.children):
        if child.kind is IRNodeKind.OMISSION:
            saw_omission = True
            new_children.append(child)
            continue
        claim = claim_by_index.get(idx)
        if saw_omission and claim is not None and child.kind is IRNodeKind.SUBSECTION:
            pruned.append(claim)
            continue
        new_children.append(child)
    if not pruned:
        return muutos_ir, ()
    return _tops._with_children(muutos_ir, new_children), tuple(pruned)

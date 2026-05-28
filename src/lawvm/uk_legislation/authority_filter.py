"""UK replay authority filtering helpers."""

from __future__ import annotations

from typing import Any, NamedTuple, Optional, Sequence

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.filter_result import FilterResult, RejectedItem, filter_result_from_parts
from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.witness_sidecars import _witness_for_op


class UKAuthorityModeDecision(NamedTuple):
    allowed: bool
    rejection_reason: Optional[str]


def _uk_op_allowed_by_authority_mode(
    op: LegalOperation,
    authority_mode: str,
) -> UKAuthorityModeDecision:
    if authority_mode != "source_text_only":
        return UKAuthorityModeDecision(allowed=True, rejection_reason=None)
    witness = _witness_for_op(op)
    extraction_witness = getattr(witness, "extraction_witness", None)
    target_expansion_witness = getattr(witness, "target_expansion_witness", None)
    authority_layer = str(getattr(extraction_witness, "authority_layer", "") or "")
    if authority_layer not in {"AFFECTING_ACT_TEXT", "AFFECTING_ACT_ENACTED_TEXT"}:
        return UKAuthorityModeDecision(
            allowed=False,
            rejection_reason="extraction_authority",
        )
    if str(getattr(target_expansion_witness, "expansion_source", "") or "") == "metadata_split":
        return UKAuthorityModeDecision(
            allowed=False,
            rejection_reason="metadata_target_expansion",
        )
    return UKAuthorityModeDecision(allowed=True, rejection_reason=None)


def _partition_uk_ops_by_authority_mode(
    ops: Sequence[LegalOperation],
    authority_mode: str,
) -> FilterResult[LegalOperation]:
    kept_ops: list[LegalOperation] = []
    rejected_items: list[RejectedItem[LegalOperation]] = []
    for op in ops:
        decision = _uk_op_allowed_by_authority_mode(op, authority_mode)
        if decision.allowed:
            kept_ops.append(op)
            continue
        rejected_items.append(
            RejectedItem(
                item=op,
                reason=decision.rejection_reason or "unspecified",
                reason_code=decision.rejection_reason or "",
            )
        )
    return filter_result_from_parts(
        accepted_items=kept_ops,
        rejected_items=rejected_items,
    )


def _uk_authority_filter_diagnostic(
    *,
    effect: UKEffectRecord,
    authority_mode: str,
    compiled_op_count: int,
    rejected_ops: Sequence[LegalOperation],
    rejected_reason_counts: dict[str, int],
    replay_applicable: bool,
    structural_for_replay: bool,
    rule_id: str = "uk_effect_authority_filter_rejected",
    blocking: bool = True,
    reason: str = "UK source-text-only authority mode rejected non-source-text replay operations",
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id=rule_id,
        family="authority_filter",
        phase="lowering",
        reason=reason,
        blocking=blocking,
        effect_id=effect.effect_id,
        affecting_act_id=effect.affecting_act_id,
        affected_provisions=effect.affected_provisions,
        affecting_provisions=effect.affecting_provisions,
        effect_type=effect.effect_type,
        authority_mode=authority_mode,
        replay_applicable=replay_applicable,
        structural_for_replay=structural_for_replay,
        applied=effect.applied,
        requires_applied=effect.requires_applied,
        metadata_only=bool(getattr(effect, "metadata_only", False)),
        rejected_op_count=len(rejected_ops),
        kept_op_count=compiled_op_count - len(rejected_ops),
        rejected_authority_layers=sorted(
            {
                str(
                    getattr(
                        getattr(_witness_for_op(op), "extraction_witness", None),
                        "authority_layer",
                        "",
                    )
                    or ""
                )
                for op in rejected_ops
                if str(
                    getattr(
                        getattr(_witness_for_op(op), "extraction_witness", None),
                        "authority_layer",
                        "",
                    )
                    or ""
                )
            }
        ),
        rejected_reasons=sorted(rejected_reason_counts),
        rejected_reason_counts=rejected_reason_counts,
    )


def _apply_uk_authority_mode(
    *,
    ops: Sequence[LegalOperation],
    effect: UKEffectRecord,
    authority_mode: str,
    replay_applicable: bool,
    structural_for_replay: bool,
    diagnostics_out: list[dict[str, Any]] | None,
    rule_id: str = "uk_effect_authority_filter_rejected",
    blocking: bool = True,
    reason: str = "UK source-text-only authority mode rejected non-source-text replay operations",
) -> list[LegalOperation]:
    partition = _partition_uk_ops_by_authority_mode(
        ops,
        authority_mode,
    )
    rejected_ops = partition.rejected_payloads
    if rejected_ops and diagnostics_out is not None:
        diagnostics_out.append(
            _uk_authority_filter_diagnostic(
                effect=effect,
                authority_mode=authority_mode,
                compiled_op_count=len(ops),
                rejected_ops=rejected_ops,
                rejected_reason_counts=partition.rejected_reason_counts(),
                replay_applicable=replay_applicable,
                structural_for_replay=structural_for_replay,
                rule_id=rule_id,
                blocking=blocking,
                reason=reason,
            )
        )
    return list(partition.accepted_items)


def _preceding_eid(op: LegalOperation) -> Optional[str]:
    witness = _witness_for_op(op)
    insertion_anchor_witness = getattr(witness, "insertion_anchor_witness", None)
    if insertion_anchor_witness is not None and insertion_anchor_witness.preceding_eid:
        return insertion_anchor_witness.preceding_eid
    return None


def _following_eid(op: LegalOperation) -> Optional[str]:
    witness = _witness_for_op(op)
    insertion_anchor_witness = getattr(witness, "insertion_anchor_witness", None)
    following = getattr(insertion_anchor_witness, "following_eid", None)
    if following:
        return str(following)
    return None

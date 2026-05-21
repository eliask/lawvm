"""UK replay authority filtering helpers."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.witness_sidecars import _witness_for_op


def _uk_op_allowed_by_authority_mode(op: LegalOperation, authority_mode: str) -> tuple[bool, Optional[str]]:
    if authority_mode != "source_text_only":
        return True, None
    witness = _witness_for_op(op)
    extraction_witness = getattr(witness, "extraction_witness", None)
    target_expansion_witness = getattr(witness, "target_expansion_witness", None)
    authority_layer = str(getattr(extraction_witness, "authority_layer", "") or "")
    if authority_layer not in {"AFFECTING_ACT_TEXT", "AFFECTING_ACT_ENACTED_TEXT"}:
        return False, "extraction_authority"
    if str(getattr(target_expansion_witness, "expansion_source", "") or "") == "metadata_split":
        return False, "metadata_target_expansion"
    return True, None


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
    return {
        "rule_id": rule_id,
        "family": "authority_filter",
        "phase": "lowering",
        "effect_id": effect.effect_id,
        "affecting_act_id": effect.affecting_act_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_provisions": effect.affecting_provisions,
        "effect_type": effect.effect_type,
        "authority_mode": authority_mode,
        "replay_applicable": replay_applicable,
        "structural_for_replay": structural_for_replay,
        "applied": effect.applied,
        "requires_applied": effect.requires_applied,
        "metadata_only": bool(getattr(effect, "metadata_only", False)),
        "rejected_op_count": len(rejected_ops),
        "kept_op_count": compiled_op_count - len(rejected_ops),
        "rejected_authority_layers": sorted(
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
        "rejected_reasons": sorted(rejected_reason_counts),
        "rejected_reason_counts": rejected_reason_counts,
        "reason": reason,
        "blocking": blocking,
        "strict_disposition": "block" if blocking else "record",
        "quirks_disposition": "record",
    }


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

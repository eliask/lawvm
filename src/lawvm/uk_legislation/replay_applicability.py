"""UK replay applicability predicates."""

from __future__ import annotations

from collections.abc import Sequence

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.effects import UKEffectRecord, uk_nonstructural_replay_candidate_family


def should_replay_nonstructural_ops(
    effect: UKEffectRecord,
    compiled_ops: Sequence[LegalOperation],
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
) -> bool:
    """Admit narrow false-negative nonstructural effect-feed rows into replay."""
    if not effect.is_applicable_for_replay(applicability_mode=applicability_mode):
        return False
    effect_type = (effect.effect_type or "").strip().lower()
    if effect_type.startswith("substituted for"):
        if effect_type in {"substituted for word", "substituted for words"}:
            return False
        if not compiled_ops:
            return False
        head, *tail = compiled_ops
        if _action_name(head.action) != "replace" or head.payload is None:
            return False
        if all(_action_name(op.action) == "replace" and op.payload is not None for op in compiled_ops):
            return True
        return all(_action_name(op.action) == "repeal" and op.target.path for op in tail)
    if effect_type.startswith("revoked"):
        return bool(compiled_ops) and all(_action_name(op.action) == "repeal" and op.target.path for op in compiled_ops)
    if effect_type.startswith("ceases to have effect"):
        return bool(compiled_ops) and all(_action_name(op.action) == "repeal" and op.target.path for op in compiled_ops)
    if effect_type == "added":
        return bool(compiled_ops) and all(
            _action_name(op.action) == "insert" and op.payload is not None
            for op in compiled_ops
        )
    if effect_type == "amended":
        return bool(compiled_ops) and all(
            _action_name(op.action) in {"text_replace", "text_repeal"}
            for op in compiled_ops
        )
    return False


def nonstructural_replay_candidate_family(
    effect: UKEffectRecord,
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
) -> str:
    """Return the nonstructural effect row family that may still replay."""
    return uk_nonstructural_replay_candidate_family(
        effect,
        applicability_mode=applicability_mode,
    )

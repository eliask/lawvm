from __future__ import annotations

from typing import Optional

from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.effects import _is_uk_repealed_by_effect_type


UK_WORD_LEVEL_EFFECT_TYPES = frozenset(
    {
        "words substituted",
        "word substituted",
        "substituted for words",
        "words repealed",
        "word repealed",
        "words omitted",
        "word omitted",
        "words inserted",
        "word inserted",
    }
)


_UK_EFFECT_TYPE_ACTIONS = {
    "inserted": "insert",
    "word inserted": "insert",
    "words inserted": "insert",
    "entry inserted": "insert",
    "added": "insert",
    "repealed": "repeal",
    "entry repealed": "repeal",
    "repealed in part": "replace",
    "words repealed": "replace",
    "word repealed": "replace",
    "substituted": "replace",
    "words substituted": "replace",
    "substituted for words": "replace",
    "word substituted": "replace",
    "replaced": "replace",
    "words omitted": "replace",
    "word omitted": "replace",
    "omitted": "repeal",
    "entry omitted": "repeal",
    "ceases to have effect": "repeal",
}


def _uk_effect_type_action(
    effect_type: str,
    *,
    has_metadata_renumber_targets: bool = False,
) -> Optional[str]:
    """Return the canonical lowering action implied by a UK effect type."""
    normalized_effect_type = str(effect_type or "").strip().lower()
    action = _UK_EFFECT_TYPE_ACTIONS.get(normalized_effect_type)
    if action is not None:
        return action
    if normalized_effect_type.startswith("substituted for"):
        return "replace"
    if _is_uk_repealed_by_effect_type(normalized_effect_type):
        return "repeal"
    if has_metadata_renumber_targets:
        return "renumber"
    return None


def _is_uk_word_level_effect_type(effect_type: str) -> bool:
    """Return True for UK effects that describe an intra-node word-level edit."""
    return str(effect_type or "").strip().lower() in UK_WORD_LEVEL_EFFECT_TYPES


def _to_structural_action(action: str) -> StructuralAction:
    """Map lowering action strings to canonical StructuralAction values."""
    if action == "replace":
        return StructuralAction.REPLACE
    if action == "text_replace":
        return StructuralAction.TEXT_REPLACE
    if action == "repeal":
        return StructuralAction.REPEAL
    if action == "text_repeal":
        return StructuralAction.TEXT_REPEAL
    if action == "insert":
        return StructuralAction.INSERT
    if action == "renumber":
        return StructuralAction.RENUMBER
    return StructuralAction.META

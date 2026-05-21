from __future__ import annotations

from lawvm.core.semantic_types import StructuralAction


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

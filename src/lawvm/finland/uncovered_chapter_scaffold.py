"""Chapter-scaffold recovery operations for Finnish uncovered-body replay."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lawvm.core.ir import IRNode, LegalAddress, OperationSource
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import StructuralAction

FI_RECOVERY_UNCOVERED_CHAPTER_SCAFFOLD_RULE_ID = "fi.recovery.uncovered_chapter_scaffold"


@dataclass(frozen=True, slots=True)
class UncoveredChapterScaffoldDraft:
    """Draft fields for a synthetic chapter LegalOperation recovery."""

    op_id: str
    path: tuple[tuple[str, str], ...]
    payload: IRNode
    source: Optional[OperationSource]
    amendment_id: str


def build_uncovered_chapter_scaffold_lo(draft: UncoveredChapterScaffoldDraft) -> _LegalOperation:
    """Build a chapter-scaffold LegalOperation with explicit recovery witness.

    Uncovered-body replay sometimes has to materialize a chapter container before
    section-level recovered ops can attach to it. That scaffold is still legal
    state, so it carries a stable recovery rule ID instead of being an anonymous
    LegalOperation side effect.
    """
    return _LegalOperation(
        op_id=draft.op_id,
        sequence=0,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=draft.path),
        payload=draft.payload,
        source=draft.source,
        group_id=f"finland-johto:{draft.amendment_id}",
        witness_rule_id=FI_RECOVERY_UNCOVERED_CHAPTER_SCAFFOLD_RULE_ID,
    )

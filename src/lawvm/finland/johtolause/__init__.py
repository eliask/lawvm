"""Finnish johtolause (enacting clause) extraction package.

Provides deterministic extraction of amendment operations from Finnish
legislative enacting clauses.

Canonical API:
    parse_clause(text) -> ClauseParseResult                  # primary public API
    extract_legal_ops(johto_text) -> List[LegalOperation]    # convenience wrapper

Op-code string format:
    "M P 5"          modify section 5
    "K P 22 5"       repeal section 22 subsection 5
    "L P 47a"        insert section 47a
    "M L 3"          modify chapter 3
"""

import re
from typing import List

from lawvm.finland.johtolause.types import ParsedOp
from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, TextPatchSpec, TextSelector
from lawvm.core.clause_ast import clause_ast_to_legal_ops
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.finland.johtolause.api import (
    _extract_text_amend_clauses,
    parse_clause,
    ClauseParseResult,
    derive_features,
)
from lawvm.finland.johtolause.surface_model import ScopeKind
from lawvm.finland.johtolause.surface_resolve import (
    ResolvedDescendantCoordination,
    ResolvedHeadingPlacement,
    ResolvedInsertion,
    ResolvedNode,
    ResolvedScopeBlock,
    ResolvedSurfaceClause,
    ResolvedTargetRef,
    ResolutionKind,
)
from lawvm.finland.ops import ScopeConfidence, lo_with_scope_confidence
from lawvm.finland.johtolause.parsed_op_clause_ast import (
    build_clause_ast,
    parsed_op_to_clause_node,
)


def _normalize_johtolause_whitespace(text: str) -> str:
    """Collapse formatting whitespace before extraction."""
    return re.sub(r"\s+", " ", text or "").strip()


def _explicit_chunk_scope_confidence_for_target(
    target: ResolvedTargetRef,
    *,
    chapter_override: str = "",
) -> ScopeConfidence | None:
    chapter = chapter_override or target.chapter
    if not chapter:
        return None
    if (
        target.resolution_witness is not None
        and target.resolution_witness.resolution_kind is not ResolutionKind.PASS_THROUGH
    ):
        return None
    return ScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source="explicit_chunk",
        confidence="explicit",
        resolved_chapter=chapter,
    )


def _resolved_node_scope_confidences(node: ResolvedNode) -> list[ScopeConfidence | None]:
    if isinstance(node, ResolvedTargetRef):
        witness = _explicit_chunk_scope_confidence_for_target(node)
        return [witness] * max(1, len(node.sub_refs))
    if isinstance(node, ResolvedInsertion):
        if (
            node.chapter
            and node.resolution_witness is not None
            and node.resolution_witness.resolution_kind is ResolutionKind.PASS_THROUGH
        ):
            return [
                ScopeConfidence(
                    tag="chapter_scope_from_explicit_chunk",
                    source="explicit_chunk",
                    confidence="explicit",
                    resolved_chapter=node.chapter,
                )
            ]
        return [None]
    if isinstance(node, ResolvedHeadingPlacement):
        if (
            node.chapter
            and node.resolution_witness is not None
            and node.resolution_witness.resolution_kind is ResolutionKind.PASS_THROUGH
        ):
            return [
                ScopeConfidence(
                    tag="chapter_scope_from_explicit_chunk",
                    source="explicit_chunk",
                    confidence="explicit",
                    resolved_chapter=node.chapter,
                )
            ]
        return [None]
    if isinstance(node, ResolvedScopeBlock):
        out: list[ScopeConfidence | None] = []
        for target in node.targets:
            if not isinstance(target, ResolvedTargetRef):
                continue
            chapter_override = node.scope_label if node.scope_kind == ScopeKind.CHAPTER else ""
            out.extend(
                [_explicit_chunk_scope_confidence_for_target(target, chapter_override=chapter_override)]
                * max(1, len(target.sub_refs))
            )
        return out
    if isinstance(node, ResolvedDescendantCoordination):
        witness = _explicit_chunk_scope_confidence_for_target(node.base)
        return [witness] * len(node.arms)
    return []


def _resolved_scope_confidences(resolved: ResolvedSurfaceClause | None) -> list[ScopeConfidence | None]:
    if resolved is None:
        return []
    out: list[ScopeConfidence | None] = []
    for verb_group in resolved.verb_groups:
        for node in verb_group.nodes:
            out.extend(_resolved_node_scope_confidences(node))
    return out


def extract_legal_ops_from_parse_result(result: ClauseParseResult) -> List[LegalOperation]:
    """Extract amendment ops from one precomputed ClauseParseResult.

    This is the Finland-local ingress seam for callers that already have the
    resolved surface/clause AST and want to avoid reparsing while preserving
    Finland-only scope-carrier transport.
    """
    ops = clause_ast_to_legal_ops(result.clause_ast)
    scope_confidences = _resolved_scope_confidences(result.resolved)
    if scope_confidences:
        if len(scope_confidences) != len(ops):
            raise RuntimeError(
                "extract_legal_ops_from_parse_result scope-confidence carrier length mismatch: "
                f"{len(scope_confidences)} resolved structural nodes vs {len(ops)} legal ops"
            )
        ops = [
            lo_with_scope_confidence(op, scope_confidence) if scope_confidence is not None else op
            for op, scope_confidence in zip(ops, scope_confidences, strict=True)
        ]
    return ops


def extract_legal_ops(johto_text: str,
                      pipeline=None) -> List[LegalOperation]:
    """Extract amendment ops as LegalOperation objects.

    The pipeline parameter is accepted for backward compatibility but ignored.

    Uses parse_clause() internally: text -> ClauseAST -> LegalOperations.
    """
    text = _normalize_johtolause_whitespace(johto_text)
    result = parse_clause(text)
    return extract_legal_ops_from_parse_result(result)


def extract_law_level_text_patch_los(
    johto_text: str,
    amendment_id: str = "",
    effective: str = "",
) -> List[LegalOperation]:
    """Extract law-level (unscoped) text patch LegalOperations from a johtolause.

    For Finnish "sana X korvataan sanalla Y" clauses without a section target,
    emit LegalOperations with empty target.path and text_patch set.

    These ops are SKIPPED by AmendmentOp.from_lo() (no structural compilation)
    but are collected by extract_law_level_text_patches() after materialization
    to apply global text replacements across the entire statute.

    Args:
        johto_text:    The normalized johtolause text.
        amendment_id:  Source amendment statute id (e.g. "2025/572"), for
                       provenance only.
        effective:     Effective date ISO string (e.g. "2025-07-01"), for
                       provenance only.

    Returns:
        List of LegalOperation objects with action=REPLACE, target.path=(),
        and text_patch set.  Empty list if no unscoped text amends found.
    """
    text = _normalize_johtolause_whitespace(johto_text)
    text_amends = _extract_text_amend_clauses(text)
    ops: List[LegalOperation] = []
    source: OperationSource | None = None
    if amendment_id:
        source = OperationSource(
            statute_id=amendment_id,
            effective=effective,
        )
    for i, ta in enumerate(text_amends):
        if ta.target is not None:
            # Section-scoped: handled by normal TextAmend pipeline.
            continue
        if not ta.old_text:
            continue
        if ta.new_text:
            patch = TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text=ta.old_text),
                replacement=ta.new_text,
            )
        else:
            patch = TextPatchSpec(
                kind=TextPatchKindEnum.DELETE,
                selector=TextSelector(match_text=ta.old_text),
            )
        ops.append(
            LegalOperation(
                op_id=f"law_level_text_patch_{i}",
                sequence=i,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=()),
                text_patch=patch,
                source=source,
            )
        )
    return ops


__all__ = [
    "parse_clause",
    "ClauseParseResult",
    "derive_features",
    "extract_legal_ops",
    "extract_legal_ops_from_parse_result",
    "extract_law_level_text_patch_los",
    "build_clause_ast",
    "parsed_op_to_clause_node",
    "ParsedOp",
    "LegalOperation",
]

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

from lawvm.core.elaboration_context import TargetUnitKind

from lawvm.finland.johtolause.types import ParsedOp
from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource, TextPatchSpec, TextSelector
from lawvm.core.clause_ast import (
    ClauseAstLoweringDiagnostic,
    clause_ast_to_legal_ops_with_diagnostics,
)
from lawvm.core.semantic_types import StructuralAction, TextPatchKindEnum
from lawvm.finland.johtolause.api import (
    _extract_text_amend_clauses,
    parse_clause,
    ClauseParseResult,
    derive_features,
)
from lawvm.finland.johtolause.surface_model import ScopeKind, SurfaceSubRef, SurfaceTextAmend
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
from lawvm.finland.ops import (
    ScopeConfidence,
    ScopeResolutionConfidence,
    ScopeResolutionSource,
    lo_with_move_destination,
    lo_with_scope_confidence,
)
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
        source=ScopeResolutionSource.EXPLICIT_CHUNK,
        confidence=ScopeResolutionConfidence.EXPLICIT,
        resolved_chapter=chapter,
    )


def _sub_ref_lowers_to_clause_node(sub_ref: SurfaceSubRef) -> bool:
    """Return false for named sub-provisions without a concrete address carrier."""
    return not (
        sub_ref.special
        and sub_ref.facet is None
        and not sub_ref.momentti
        and not sub_ref.item
    )


def _lowerable_sub_refs(sub_refs: tuple[SurfaceSubRef, ...]) -> tuple[SurfaceSubRef | None, ...]:
    if not sub_refs:
        return (None,)
    return tuple(sub_ref for sub_ref in sub_refs if _sub_ref_lowers_to_clause_node(sub_ref))


def _resolved_node_scope_confidences(node: ResolvedNode) -> list[ScopeConfidence | None]:
    if isinstance(node, ResolvedTargetRef):
        witness = _explicit_chunk_scope_confidence_for_target(node)
        return [witness] * len(_lowerable_sub_refs(node.sub_refs))
    if isinstance(node, ResolvedInsertion):
        if (
            node.chapter
            and node.resolution_witness is not None
            and node.resolution_witness.resolution_kind is ResolutionKind.PASS_THROUGH
        ):
            return [
                ScopeConfidence(
                    tag="chapter_scope_from_explicit_chunk",
                    source=ScopeResolutionSource.EXPLICIT_CHUNK,
                    confidence=ScopeResolutionConfidence.EXPLICIT,
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
                    source=ScopeResolutionSource.EXPLICIT_CHUNK,
                    confidence=ScopeResolutionConfidence.EXPLICIT,
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
                * len(_lowerable_sub_refs(target.sub_refs))
            )
        return out
    if isinstance(node, ResolvedDescendantCoordination):
        witness = _explicit_chunk_scope_confidence_for_target(node.base)
        return [
            witness
            for arm in node.arms
            if _sub_ref_lowers_to_clause_node(arm)
        ]
    return []


def _move_clause_kind_for_target(target: ResolvedTargetRef) -> TargetUnitKind | None:
    """Return the move-rider destination kind for one whole-section target ref.

    Mirrors the surface-lowering stamping conditions: only a SECTION target
    whose sub-reference is the empty whole-section slot carries the rider.
    Facet/subsection/item arms never receive move-rider semantics.
    """
    kind = target.move_clause_target_unit_kind
    if kind not in ("chapter", "part"):
        return None
    if target.kind.value != "P":
        return None
    return kind


def _resolved_node_move_clause_kinds(node: ResolvedNode) -> list[TargetUnitKind | None]:
    """Per-op move-rider destination kinds, 1:1 with the scope-confidence walk.

    The surface parser resolves "X §, joka samalla siirretään Y lukuun" by
    rewriting the target chapter to the DESTINATION and stamping
    ``move_clause_target_unit_kind`` on the resolved surface ref. The native
    ClauseAST lowering intentionally keeps core nodes field-free, so the rider
    must be re-attached at this Finland bridge or it is silently dropped
    before compile (the bug class: a destination-scoped REPLACE whose move
    semantics are invisible to apply).
    """
    if isinstance(node, ResolvedTargetRef):
        per_subref: list[TargetUnitKind | None] = []
        sub_refs = _lowerable_sub_refs(node.sub_refs)
        for sr in sub_refs:
            whole_section = sr is None or (
                not sr.momentti and not sr.item and sr.facet is None
            )
            per_subref.append(_move_clause_kind_for_target(node) if whole_section else None)
        return per_subref
    if isinstance(node, ResolvedInsertion):
        return [None]
    if isinstance(node, ResolvedHeadingPlacement):
        return [None]
    if isinstance(node, ResolvedScopeBlock):
        out: list[TargetUnitKind | None] = []
        for target in node.targets:
            if not isinstance(target, ResolvedTargetRef):
                continue
            sub_refs = _lowerable_sub_refs(target.sub_refs)
            for sr in sub_refs:
                whole_section = sr is None or (
                    not sr.momentti and not sr.item and sr.facet is None
                )
                out.append(_move_clause_kind_for_target(target) if whole_section else None)
        return out
    if isinstance(node, ResolvedDescendantCoordination):
        return [
            None
            for arm in node.arms
            if _sub_ref_lowers_to_clause_node(arm)
        ]
    return []


def _resolved_move_clause_kinds(resolved: ResolvedSurfaceClause | None) -> list[TargetUnitKind | None]:
    if resolved is None:
        return []
    out: list[TargetUnitKind | None] = []
    for verb_group in resolved.verb_groups:
        for node in verb_group.nodes:
            out.extend(_resolved_node_move_clause_kinds(node))
    return out


def _resolved_scope_confidences(resolved: ResolvedSurfaceClause | None) -> list[ScopeConfidence | None]:
    if resolved is None:
        return []
    out: list[ScopeConfidence | None] = []
    for verb_group in resolved.verb_groups:
        for node in verb_group.nodes:
            out.extend(_resolved_node_scope_confidences(node))
    return out


def extract_legal_ops_from_parse_result(
    result: ClauseParseResult,
    diagnostics_out: List[ClauseAstLoweringDiagnostic] | None = None,
) -> List[LegalOperation]:
    """Extract amendment ops from one precomputed ClauseParseResult.

    This is the Finland-local ingress seam for callers that already have the
    resolved surface/clause AST and want to avoid reparsing while preserving
    Finland-only scope-carrier transport.

    The seam routes through ``clause_ast_to_legal_ops_with_diagnostics`` so that
    every clause node generic core lowering cannot own (MetaClause,
    ItemShiftClause, NamedRowClause) becomes a typed
    ``ClauseAstLoweringDiagnostic`` receipt instead of a silent drop. The op
    list is identical to the silent variant — the diagnostics carry only the
    already-dropped nodes — so downstream replay and the scope-carrier length
    invariant are unchanged. Callers that pass ``diagnostics_out`` collect the
    receipts; the compile path projects them into the governed finding ledger.
    """
    ops, lowering_diagnostics = clause_ast_to_legal_ops_with_diagnostics(result.clause_ast)
    if diagnostics_out is not None:
        diagnostics_out.extend(lowering_diagnostics)
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
    move_clause_kinds = _resolved_move_clause_kinds(result.resolved)
    if move_clause_kinds:
        if len(move_clause_kinds) != len(ops):
            raise RuntimeError(
                "extract_legal_ops_from_parse_result move-clause carrier length mismatch: "
                f"{len(move_clause_kinds)} resolved structural nodes vs {len(ops)} legal ops"
            )
        ops = [
            lo_with_move_destination(op, kind) if kind is not None else op
            for op, kind in zip(ops, move_clause_kinds, strict=True)
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


def _raw_text_for_text_amend(johto_text: str, ta: SurfaceTextAmend) -> str:
    """Per-op verbatim source substring for one text-amend op (task #50).

    Returns the verbatim ``johto_text`` slice spanning from the ``old_text``
    token through the ``new_text`` token, so each per-op ``LegalOperation.raw_text``
    differs from its siblings and ``compute_source_anchor(raw_bytes,
    clause_text=op.raw_text)`` can land a per-op byte anchor that
    distinguishes which clause produced which op. When the phrase-boundary
    search fails the function falls back to ``ta.old_text`` alone (still
    verbatim, though short — ``compute_source_anchor`` will refuse, not
    guess, when the substring is ambiguous: §1.10 fail-loud, §0 no
    fabricated anchors). Empty when the op has no verbatim anchor.

    The search uses the QUOTED form ``"old_text"``/``"new_text"`` because
    the regex extractor (``_extract_text_amend_clauses``) strips the
    surrounding quotes from ``old``/``new`` capture groups. Re-attaching
    them here unambiguously locates the AMEND-PHRASE occurrence
    (``sana "X" korvataan sanalla "Y"``) rather than any stray prose mention
    of the same word — the quotes are required syntax of Finnish text-amend
    prose, so the per-op anchor always lands at the clause, not at prose.

    This is the lightest source-anchor seam (Option C) — the per-op
    ``raw_text`` is evidence footing (§2.10 Source plane) consumable only
    through a typed ``SourceAnchor`` later; it is not itself replay
    authority (§1.11, §1.12).
    """
    if not ta.old_text:
        return ""
    # Search the quoted amend-phrase marker. The extractor strips quotes, so
    # we re-attach them to disambiguate the clause occurrence from any stray
    # prose mention of the bare word.
    quoted_old = f'"{ta.old_text}"'
    old_idx = johto_text.find(quoted_old)
    if old_idx < 0:
        # Fall back to the bare word if the quoted form is absent (still
        # verbatim — may be ambiguous downstream; compute_source_anchor
        # will refuse rather than guess when that happens).
        return ta.old_text
    if not ta.new_text:
        return johto_text[old_idx : old_idx + len(quoted_old)]
    quoted_new = f'"{ta.new_text}"'
    new_idx = johto_text.find(quoted_new, old_idx + len(quoted_old))
    if new_idx < 0:
        return ta.old_text
    return johto_text[old_idx : new_idx + len(quoted_new)]


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

    Each minted op carries a per-op ``raw_text`` — the verbatim substring of
    ``johto_text`` that produced THIS op — so downstream
    ``compute_source_anchor`` can land a distinct per-op ``SourceAnchor``
    (task #50): the receipt ``source_anchor`` then carries the byte span of
    the SPECIFIC clause, not the whole-johtolause amendment-level span.

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
                # Per-op verbatim source substring (task #50 Option C): the
                # clause-phrase slice of `johto_text` from `old_text` through
                # `new_text`, distinct per amend so the per-op SourceAnchor
                # computes a per-clause byte span rather than the
                # amendment-level span. Empty fail-safe `""` is honoured by
                # downstream anchor computators (they return None — fail loud).
                raw_text=_raw_text_for_text_amend(text, ta),
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

"""api — Canonical public API for the Finnish amendment clause parser.

This module owns:
  - parse_clause()      — canonical public API (structural + meta)
  - ClauseParseResult   — result type
  - derive_features()   — feature-tag derivation for curated test tracking

Authority path:
  tokens → surface_parse.parse() → SurfaceClause
    → surface_resolve.resolve_surface_clause() → ResolvedSurfaceClause
    → lower_clause_ast.lower_to_clause_ast() → ClauseAST

ParsedOps are derived from ClauseAST via clause_ast_to_legal_ops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Sequence

from lawvm.core.frontend_contract import (
    DerivedCompatibilityArtifact,
    FrontendCapability,
    SurfaceParseResult,
)
from lawvm.core.frontend_phase_surface import (
    FrontendDiagnostic,
    FrontendPhaseRow,
    FrontendPhaseSurface,
    frontend_diagnostic_findings,
)
from lawvm.core.ir import LegalAddress
from lawvm.core.phase_result import Finding
from lawvm.core.token_tape import TokenLexeme, TokenTape
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.finland.johtolause.surface_model import TargetKind
from lawvm.core.clause_ast import ClauseAST
from lawvm.core.semantic_types import FacetKind, LabelAction

if TYPE_CHECKING:
    from lawvm.finland.johtolause.surface_model import (
        SurfaceClause as _SurfaceClauseType,
        SurfaceNode as _SurfaceNodeType,
    )
    from lawvm.finland.johtolause.surface_resolve import ResolvedSurfaceClause as _ResolvedSurfaceClauseType


FINLAND_JOHTOLAUSE_FRONTEND_ID = "finland.johtolause.parse_clause"
FINLAND_JOHTOLAUSE_FRONTEND_CAPABILITY = FrontendCapability(
    frontend_id=FINLAND_JOHTOLAUSE_FRONTEND_ID,
    jurisdiction="fi",
    scope="clause_compiler_spine",
    status="reference_clause_compiler",
    has_token_tape=True,
    has_annotation_overlay=True,
    has_surface_clause=True,
    has_enriched_surface=True,
    has_resolved_surface=True,
    has_clause_ast=True,
    has_payload_surface=False,
    has_payload_elaboration=False,
    has_canonical_effects=False,
    has_replay_apply=False,
    has_materialization=False,
    has_agreement_surface=False,
    compatibility_outputs=("ParsedOp",),
    phase_names=(
        "tokenize",
        "scan_annotations",
        "surface_parse",
        "surface_enrichment",
        "surface_resolve",
        "clause_ast_lowering",
        "parsed_ops_compat",
        "residual_collection",
    ),
    caveats=(
        "capability_is_scoped_to_clause_parsing_not_full_finland_replay",
        "parsed_ops_are_compatibility_output_not_primary_authority",
        "capability_declaration_does_not_authorize_replay",
    ),
)


def infer_move_clause_target_unit_kind(
    destination: LegalAddress | None,
) -> Literal["chapter", "part"] | None:
    """Infer the move-tail container kind from a destination address.

    The bridge keeps move-tail state at the Finland boundary. Core ClauseAST
    nodes stay field-free; only the Finland ParsedOp bridge carries the typed
    move-tail destination kind.
    """
    if destination is None:
        return None
    destination_parts = dict(destination.path)
    if destination_parts.get("part"):
        return "part"
    if destination_parts.get("chapter"):
        return "chapter"
    return None


# ═══════════════════════════════════════════════════════════════════════
# ClauseParseResult — canonical public parse output
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ClauseParseResult:
    """Result of parsing a Finnish johtolause through the full pipeline.

    The resolver and lowerer are total functions — they never raise on valid
    input.  If they crash, that is a programming bug and propagates to the
    caller rather than being silently swallowed.

    Fields:
        clause_ast:              The ClauseAST produced by native lowering from
                                 ResolvedSurfaceClause.  This is the PRIMARY output.
        surface_clause:          The ORIGINAL parser-emitted SurfaceClause, exactly
                                 as produced by surface_parse().  No post-parse
                                 enrichment (jolloin renumber, meta clauses, text
                                 amend clauses) has been applied.
        enriched_surface_clause: The SurfaceClause after all post-parse enrichment
                                 (jolloin renumber pairs, meta clauses, text amend
                                 clauses).  This is what was actually passed to the
                                 resolver and lowered to ClauseAST.  None when no
                                 enrichment was needed (i.e. identical to
                                 surface_clause).
        resolved:                The ResolvedSurfaceClause after source-local resolution.
        parsed_ops:              Flat ParsedOp list derived from ClauseAST (compat).
        residuals:               Unconsumed tokens / unresolved nodes from the parse.
        diagnostics:             Human-readable diagnostic strings.
        meta_clauses:            Meta/effect clauses extracted from the same text.
        supplementary_clauses:   SurfaceMetaClause and SurfaceTextAmend instances that
                                 are NOT part of any structural verb group.
        target_version_bindings: Finland-local cited-version selector sidecars
                                 preserved from provenance text.
        phase_surface:          Typed report-facing phase surface.  It names
                                 the compiler waists and marks ParsedOps as a
                                 compatibility projection, not semantic authority.
        surface_result:         Shared surface-parse waist projection recording
                                 original/enriched/resolved status.
        compatibility_artifacts:
                                Typed certificates for derived compatibility
                                artifacts such as ParsedOps.
        findings:               Governed core Finding projection of typed
                                 frontend diagnostics.
        typed_diagnostics:      Typed diagnostic rows backing phase_surface.
    """

    clause_ast: ClauseAST
    surface_clause: _SurfaceClauseType | None = None
    enriched_surface_clause: _SurfaceClauseType | None = None
    resolved: _ResolvedSurfaceClauseType | None = None
    parsed_ops: list[ParsedOp] = field(default_factory=list)
    residuals: list = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    meta_clauses: tuple = ()
    supplementary_clauses: tuple = ()
    target_version_bindings: tuple = ()
    parse_error: str | None = None
    lowering_diagnostics: tuple = ()
    phase_surface: FrontendPhaseSurface | None = None
    surface_result: SurfaceParseResult | None = None
    compatibility_artifacts: tuple[DerivedCompatibilityArtifact, ...] = ()
    findings: tuple[Finding, ...] = ()
    typed_diagnostics: tuple[FrontendDiagnostic, ...] = ()

    @property
    def is_failed(self) -> bool:
        """True when the resolver or lowerer crashed and parse_error is set."""
        return self.parse_error is not None


# ═══════════════════════════════════════════════════════════════════════
# parse_clause — canonical public API
# ═══════════════════════════════════════════════════════════════════════


def parse_clause(text: str, *, statute_id: str = "") -> ClauseParseResult:
    """Parse a Finnish amendment johtolause to ClauseAST.

    Authority path:
        text
          -> tokenize + apply_annotations
          -> surface_parse.parse()               -> SurfaceClause (real)
          -> resolve_surface_clause()             -> ResolvedSurfaceClause (real)
          -> lower_to_clause_ast()                -> ClauseAST (native)
          -> _derive_parsed_ops_from_ast()        -> list[ParsedOp] (compat)

    No legacy bridge modules.  No hidden middle authority.
    """
    from lawvm.finland.johtolause.surface_parse import parse as surface_parse
    from lawvm.finland.johtolause.surface_resolve import resolve_surface_clause
    from lawvm.finland.johtolause.lower_clause_ast import lower_to_clause_ast_with_diagnostics
    from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses
    from lawvm.finland.johtolause.scan import (
        apply_annotations_with_jolloin_pairs,
        extract_target_version_bindings,
    )
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.surface_model import (
        SurfaceClause as SurfaceClauseModel,
    )

    raw_tokens = tokenize(text)
    core_token_tape = _core_token_tape_from_finland_tokens(text, raw_tokens)
    target_version_bindings = extract_target_version_bindings(raw_tokens)
    tokens, _jolloin_pairs = apply_annotations_with_jolloin_pairs(raw_tokens)

    # -- Phase 1: Parse -> real SurfaceClause --
    # This is the ORIGINAL parser output — preserved unmodified, except that
    # we correct source_text to be the verbatim original text passed to us.
    # surface_parse() reconstructs source_text from filtered tokens which
    # loses hidden spans, punctuation, and exact source identity (Pro audit d-#3).
    #
    # Jolloin renumber pairs are passed natively to the parser (e-#1/#2 fix):
    # when the parser encounters a JOLLOIN_MOVE sentinel with renumber data,
    # it emits SurfaceTargetRef + SurfaceRenumberTail nodes directly in a
    # SIIRTAA verb group, prepended to the clause's verb groups.
    _parsed = surface_parse(tokens, jolloin_renumber_pairs=_jolloin_pairs if _jolloin_pairs else None)
    if _parsed.source_text != text:
        original_surface_clause = SurfaceClauseModel(
            verb_groups=_parsed.verb_groups,
            meta_clauses=_parsed.meta_clauses,
            text_amend_clauses=_parsed.text_amend_clauses,
            target_version_bindings=target_version_bindings,
            source_text=text,
            consumed_count=_parsed.consumed_count,
        )
    else:
        original_surface_clause = SurfaceClauseModel(
            verb_groups=_parsed.verb_groups,
            meta_clauses=_parsed.meta_clauses,
            text_amend_clauses=_parsed.text_amend_clauses,
            target_version_bindings=target_version_bindings,
            source_text=_parsed.source_text,
            consumed_count=_parsed.consumed_count,
        )

    # -- Phase 1b–1d: Build enriched SurfaceClause --
    # Post-parse enrichment adds meta clauses and text amend clauses.  The
    # enriched version is what flows to the resolver and downstream pipeline.
    # The original is preserved for architectural honesty: consumers can see
    # exactly what the parser produced vs. what was injected later.
    enriched = original_surface_clause
    was_enriched = False

    # Phase 1b: Jolloin renumber pairs are now emitted natively by the parser
    # (e-#1/#2 Pro audit fix).  surface_parse() receives the renumber pair map
    # from apply_annotations_with_jolloin_pairs() and prepends a SIIRTAA verb
    # group with SurfaceTargetRef + SurfaceRenumberTail nodes when it encounters
    # a JOLLOIN_MOVE sentinel with renumber data.  No post-hoc enrichment needed.

    # Phase 1c: Set meta clauses on the SurfaceClause top-level field.
    # Meta clauses are supplementary — they are NOT part of any structural verb
    # group.  They are placed in meta_clauses on SurfaceClause (not mixed into
    # verb_groups.nodes) so the resolver and lowerer can process them as a
    # separate plane alongside the structural verb groups.
    from lawvm.finland.johtolause.surface_model import SurfaceMetaClause

    meta_nodes = extract_meta_surface_clauses(text)
    if meta_nodes:
        enriched = SurfaceClauseModel(
            verb_groups=enriched.verb_groups,
            meta_clauses=tuple(meta_nodes),
            text_amend_clauses=enriched.text_amend_clauses,
            target_version_bindings=enriched.target_version_bindings,
            source_text=enriched.source_text,
            consumed_count=enriched.consumed_count,
        )
        was_enriched = True

    # Phase 1d: Set text amend clauses on the SurfaceClause top-level field.
    # Same separation contract as meta clauses: supplementary, not structural.
    text_amend_nodes = _extract_text_amend_clauses(text)
    if text_amend_nodes:
        enriched = SurfaceClauseModel(
            verb_groups=enriched.verb_groups,
            meta_clauses=enriched.meta_clauses,
            text_amend_clauses=tuple(text_amend_nodes),
            target_version_bindings=enriched.target_version_bindings,
            source_text=enriched.source_text,
            consumed_count=enriched.consumed_count,
        )
        was_enriched = True

    # Collect all supplementary nodes — these are the meta + text-amend nodes
    # that are NOT part of any structural verb group.
    supplementary_nodes: tuple = tuple(list(meta_nodes) + list(text_amend_nodes))

    enriched_surface_clause = enriched if was_enriched else None

    # -- Collect diagnostics (initialized early for error paths) --
    diagnostics: list[str] = []

    # -- Phase 2: Resolve -> ResolvedSurfaceClause --
    # RuntimeError is a known internal pipeline error — caught and reported
    # as a diagnostic.  Programming bugs (TypeError, AttributeError, etc.)
    # propagate to the caller so they are not silently swallowed.
    resolve_input = enriched_surface_clause if enriched_surface_clause is not None else original_surface_clause
    parse_error: str | None = None
    internal_error_phase: str | None = None
    try:
        resolved = resolve_surface_clause(resolve_input)
    except RuntimeError as exc:
        resolved = None
        _err = f"resolve_error: {type(exc).__name__}: {exc}"
        parse_error = _err
        internal_error_phase = "surface_resolve"
        diagnostics.append(f"internal_error: resolve: {type(exc).__name__}: {exc}")

    # -- Phase 3: Lower -> ClauseAST (native) --
    # Same contract: RuntimeError is caught and reported; other exceptions propagate.
    if resolved is not None:
        try:
            clause_ast, lowering_diagnostics = lower_to_clause_ast_with_diagnostics(resolved)
        except RuntimeError as exc:
            clause_ast = ClauseAST(verb_groups=(), source_text=text)
            lowering_diagnostics = ()
            _err = f"lower_error: {type(exc).__name__}: {exc}"
            parse_error = _err
            internal_error_phase = "clause_ast_lowering"
            diagnostics.append(f"internal_error: lower: {type(exc).__name__}: {exc}")
    else:
        clause_ast = ClauseAST(verb_groups=(), source_text=text)
        lowering_diagnostics = ()

    # -- Derive ParsedOps from ClauseAST --
    ops = _derive_parsed_ops_from_ast(clause_ast)
    compatibility_artifacts = (
        _build_parsed_ops_compatibility_artifact(
            source_hash=core_token_tape.source_hash,
            parsed_ops=ops,
            clause_ast=clause_ast,
        ),
    )

    residuals: list = []

    # -- Collect token residuals (tokens beyond consumed_count) --
    # consumed_count is set on the ORIGINAL surface clause (not the enriched one,
    # which may have injected synthetic nodes that don't correspond to input tokens).
    if original_surface_clause.consumed_count < len(tokens):
        leftover_tokens = list(tokens[original_surface_clause.consumed_count :])
        residuals.append({"kind": "unconsumed_tokens", "tokens": leftover_tokens})

    # -- Collect resolver residuals (SurfaceNodes that couldn't be resolved) --
    if resolved is not None and resolved.residuals:
        residuals.append({"kind": "unresolved_nodes", "nodes": list(resolved.residuals)})
    if lowering_diagnostics:
        residuals.append({
            "kind": "lowering_diagnostics",
            "diagnostics": list(lowering_diagnostics),
        })

    if statute_id:
        diagnostics.append(f"statute_id={statute_id!r}")

    # Derive meta_clauses from ClauseAST (they now flow through the pipeline).
    # Fall back to direct extraction if the ClauseAST path didn't produce them.
    from lawvm.core.clause_ast import MetaClause as ClauseASTMetaClause

    ast_meta = []
    for vg in clause_ast.verb_groups:
        for node in vg.nodes:
            if isinstance(node, ClauseASTMetaClause):
                ast_meta.append(
                    SurfaceMetaClause(
                        kind=node.kind,  # MetaClauseKind enum
                        text=node.raw_text,
                    )
                )
    meta_clauses = tuple(ast_meta) if ast_meta else tuple(extract_meta_surface_clauses(text))

    phase_surface = _build_finland_clause_phase_surface(
        text=text,
        token_tape=core_token_tape,
        raw_token_count=len(raw_tokens),
        structural_token_count=len(tokens),
        jolloin_pair_count=len(_jolloin_pairs),
        target_version_binding_count=len(target_version_bindings),
        original_surface_clause=original_surface_clause,
        enriched_surface_clause=enriched_surface_clause,
        resolved=resolved,
        clause_ast=clause_ast,
        parsed_ops=ops,
        compatibility_artifacts=compatibility_artifacts,
        residuals=residuals,
        diagnostics=diagnostics,
        parse_error=parse_error,
        internal_error_phase=internal_error_phase,
        lowering_diagnostics=lowering_diagnostics,
        meta_clause_count=len(meta_nodes),
        text_amend_clause_count=len(text_amend_nodes),
        supplementary_clause_count=len(supplementary_nodes),
    )
    surface_result = _build_finland_surface_parse_result(
        source_hash=core_token_tape.source_hash,
        original_surface_clause=original_surface_clause,
        enriched_surface_clause=enriched_surface_clause,
        resolved=resolved,
        meta_clause_count=len(meta_nodes),
        text_amend_clause_count=len(text_amend_nodes),
        supplementary_nodes=supplementary_nodes,
        diagnostic_ids=tuple(diagnostic.diagnostic_id for diagnostic in phase_surface.diagnostics),
    )
    findings = frontend_diagnostic_findings(phase_surface.diagnostics)

    return ClauseParseResult(
        clause_ast=clause_ast,
        surface_clause=original_surface_clause,
        enriched_surface_clause=enriched_surface_clause,
        resolved=resolved,
        parsed_ops=ops,
        residuals=residuals,
        diagnostics=diagnostics,
        meta_clauses=meta_clauses,
        parse_error=parse_error,
        supplementary_clauses=supplementary_nodes,
        target_version_bindings=resolve_input.target_version_bindings,
        lowering_diagnostics=lowering_diagnostics,
        phase_surface=phase_surface,
        surface_result=surface_result,
        compatibility_artifacts=compatibility_artifacts,
        findings=findings,
        typed_diagnostics=phase_surface.diagnostics,
    )


def _build_finland_clause_phase_surface(
    *,
    text: str,
    token_tape: TokenTape,
    raw_token_count: int,
    structural_token_count: int,
    jolloin_pair_count: int,
    target_version_binding_count: int,
    original_surface_clause: _SurfaceClauseType,
    enriched_surface_clause: _SurfaceClauseType | None,
    resolved: _ResolvedSurfaceClauseType | None,
    clause_ast: ClauseAST,
    parsed_ops: list[ParsedOp],
    compatibility_artifacts: tuple[DerivedCompatibilityArtifact, ...],
    residuals: list,
    diagnostics: list[str],
    parse_error: str | None,
    internal_error_phase: str | None,
    lowering_diagnostics: tuple,
    meta_clause_count: int,
    text_amend_clause_count: int,
    supplementary_clause_count: int,
) -> FrontendPhaseSurface:
    typed_diagnostics = _build_finland_frontend_diagnostics(
        diagnostics=diagnostics,
        residuals=residuals,
        parse_error=parse_error,
        internal_error_phase=internal_error_phase,
        lowering_diagnostics=lowering_diagnostics,
    )
    diagnostic_ids_by_phase: dict[str, list[str]] = {}
    for diagnostic in typed_diagnostics:
        diagnostic_ids_by_phase.setdefault(diagnostic.phase, []).append(diagnostic.diagnostic_id)

    def row(
        phase: str,
        status: str,
        artifact_kind: str,
        authority_role: str,
        produced: bool,
        *,
        input_artifacts: tuple[str, ...] = (),
        output_artifacts: tuple[str, ...] = (),
        detail: dict[str, Any] | None = None,
    ) -> FrontendPhaseRow:
        return FrontendPhaseRow(
            phase=phase,
            status=status,
            artifact_kind=artifact_kind,
            authority_role=authority_role,
            produced=produced,
            input_artifacts=input_artifacts,
            output_artifacts=output_artifacts,
            diagnostic_ids=tuple(diagnostic_ids_by_phase.get(phase, ())),
            detail=detail or {},
        )

    phase_rows = (
        row(
            "tokenize",
            "produced",
            "token_tape",
            "source_witness",
            True,
            input_artifacts=("source_text",),
            output_artifacts=("raw_token_tape",),
            detail={
                "token_tape_schema": token_tape.tape_schema,
                "token_tape_source_hash": token_tape.source_hash,
                "token_tape_lexeme_count": len(token_tape),
                "raw_token_count": raw_token_count,
                "source_length": len(text),
            },
        ),
        row(
            "scan_annotations",
            "produced",
            "annotated_token_view",
            "source_preserving_annotation_overlay",
            True,
            input_artifacts=("raw_token_tape",),
            output_artifacts=("structural_token_view",),
            detail={
                "raw_token_count": raw_token_count,
                "structural_token_count": structural_token_count,
                "jolloin_pair_count": jolloin_pair_count,
                "target_version_binding_count": target_version_binding_count,
            },
        ),
        row(
            "surface_parse",
            "produced",
            "SurfaceClause",
            "original_surface_parser_output",
            original_surface_clause is not None,
            input_artifacts=("structural_token_view",),
            output_artifacts=("surface_clause",),
            detail={
                "verb_group_count": len(original_surface_clause.verb_groups),
                "consumed_count": original_surface_clause.consumed_count,
            },
        ),
        row(
            "surface_enrichment",
            "enriched" if enriched_surface_clause is not None else "identity",
            "SurfaceClause",
            "enrichment_projection_not_source_authority",
            True,
            input_artifacts=("surface_clause",),
            output_artifacts=("resolver_surface_clause",),
            detail={
                "meta_clause_count": meta_clause_count,
                "text_amend_clause_count": text_amend_clause_count,
                "supplementary_clause_count": supplementary_clause_count,
            },
        ),
        row(
            "surface_resolve",
            "resolved" if resolved is not None else "failed",
            "ResolvedSurfaceClause",
            "source_local_resolution",
            resolved is not None,
            input_artifacts=("resolver_surface_clause",),
            output_artifacts=("resolved_surface_clause",) if resolved is not None else (),
            detail={
                "residual_count": len(resolved.residuals) if resolved is not None else 0,
            },
        ),
        row(
            "clause_ast_lowering",
            "lowered" if not parse_error else "failed",
            "ClauseAST",
            "primary_semantic_authority",
            True,
            input_artifacts=("resolved_surface_clause",) if resolved is not None else (),
            output_artifacts=("clause_ast",),
            detail={
                "verb_group_count": len(clause_ast.verb_groups),
                "lowering_diagnostic_count": len(lowering_diagnostics),
            },
        ),
        row(
            "parsed_ops_compat",
            "derived",
            "ParsedOp",
            "compatibility_projection_not_authority",
            True,
            input_artifacts=("clause_ast",),
            output_artifacts=("parsed_ops",),
            detail={
                "parsed_op_count": len(parsed_ops),
                "compatibility_artifacts": tuple(
                    artifact.to_dict() for artifact in compatibility_artifacts
                ),
            },
        ),
        row(
            "residual_collection",
            "residuals_present" if residuals else "clean",
            "FrontendResiduals",
            "diagnostic_residual_surface",
            True,
            input_artifacts=("surface_clause", "resolved_surface_clause", "clause_ast"),
            output_artifacts=("residuals",),
            detail={
                "residual_count": len(residuals),
                "residual_kinds": sorted(str(entry.get("kind", "unknown")) for entry in residuals if isinstance(entry, dict)),
            },
        ),
    )

    return FrontendPhaseSurface(
        jurisdiction="fi",
        frontend=FINLAND_JOHTOLAUSE_FRONTEND_ID,
        schema="lawvm.frontend_phase_surface.v1",
        truth_claim=(
            "ClauseAST is the primary semantic parser output; ParsedOps are a "
            "compatibility projection and this phase surface does not authorize replay."
        ),
        source_hash=token_tape.source_hash,
        source_length=len(text),
        authority_path=(
            "source_text",
            "raw_token_tape",
            "structural_token_view",
            "SurfaceClause",
            "ResolvedSurfaceClause",
            "ClauseAST",
        ),
        compatibility_outputs=("ParsedOp",),
        phase_rows=phase_rows,
        diagnostics=typed_diagnostics,
        replay_claims=False,
        canonical_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        detail={
            "frontend_capability_id": FINLAND_JOHTOLAUSE_FRONTEND_CAPABILITY.frontend_id,
            "frontend_capability_status": FINLAND_JOHTOLAUSE_FRONTEND_CAPABILITY.status,
            "frontend_capability_scope": FINLAND_JOHTOLAUSE_FRONTEND_CAPABILITY.scope,
            "parsed_ops_are_compatibility_output": True,
            "compatibility_artifacts": tuple(
                artifact.to_dict() for artifact in compatibility_artifacts
            ),
            "clause_ast_is_primary_semantic_output": True,
        },
    )


def _build_parsed_ops_compatibility_artifact(
    *,
    source_hash: str,
    parsed_ops: list[ParsedOp],
    clause_ast: ClauseAST,
) -> DerivedCompatibilityArtifact:
    return DerivedCompatibilityArtifact(
        artifact_id=f"fi:johtolause:{source_hash}:parsed_ops",
        jurisdiction="fi",
        frontend_id=FINLAND_JOHTOLAUSE_FRONTEND_ID,
        artifact_kind="ParsedOp",
        source_artifact_id=f"fi:johtolause:{source_hash}:clause_ast",
        source_artifact_kind="ClauseAST",
        derivation_phase="parsed_ops_compat",
        status="derived_compatibility_projection",
        lossy=True,
        preserved_fields=(
            "operation_kind",
            "target_reference",
            "verb_group_order",
            "facet",
        ),
        lost_fields=(
            "native_clause_ast_node_identity",
            "supplementary_meta_clause_authority",
            "text_amend_clause_authority",
        ),
        input_artifacts=("clause_ast",),
        output_artifacts=("parsed_ops",),
        replay_authorized=False,
        semantic_authority=False,
        detail={
            "parsed_op_count": len(parsed_ops),
            "clause_ast_verb_group_count": len(clause_ast.verb_groups),
            "primary_authority": "ClauseAST",
            "compatibility_projection_only": True,
        },
    )


def _build_finland_surface_parse_result(
    *,
    source_hash: str,
    original_surface_clause: _SurfaceClauseType,
    enriched_surface_clause: _SurfaceClauseType | None,
    resolved: _ResolvedSurfaceClauseType | None,
    meta_clause_count: int,
    text_amend_clause_count: int,
    supplementary_nodes: tuple,
    diagnostic_ids: tuple[str, ...],
) -> SurfaceParseResult:
    enrichment_rule_ids: list[str] = []
    if meta_clause_count:
        enrichment_rule_ids.append("fi.surface_enrichment.meta_clauses.v1")
    if text_amend_clause_count:
        enrichment_rule_ids.append("fi.surface_enrichment.text_amend_clauses.v1")
    status = "resolved" if resolved is not None else "unresolved"
    if enriched_surface_clause is not None:
        status = f"enriched_{status}"
    return SurfaceParseResult(
        frontend_id=FINLAND_JOHTOLAUSE_FRONTEND_ID,
        jurisdiction="fi",
        source_hash=source_hash,
        status=status,
        original_surface_kind=type(original_surface_clause).__name__,
        original_produced=True,
        enriched_surface_kind=type(enriched_surface_clause).__name__ if enriched_surface_clause is not None else "",
        enriched=enriched_surface_clause is not None,
        resolved_surface_kind=type(resolved).__name__ if resolved is not None else "",
        resolved_produced=resolved is not None,
        consumed_count=original_surface_clause.consumed_count,
        enrichment_rule_ids=tuple(enrichment_rule_ids),
        supplementary_surface_kinds=tuple(type(node).__name__ for node in supplementary_nodes),
        diagnostic_ids=diagnostic_ids,
        detail={
            "meta_clause_count": meta_clause_count,
            "text_amend_clause_count": text_amend_clause_count,
            "supplementary_clause_count": len(supplementary_nodes),
            "original_surface_preserved": True,
            "resolver_consumed_enriched_surface": enriched_surface_clause is not None,
        },
    )


def _core_token_tape_from_finland_tokens(text: str, tokens: Sequence[object]) -> TokenTape:
    lexemes: list[TokenLexeme] = []
    for token in tokens:
        verb_code = getattr(token, "verb_code", None)
        semantic_code = getattr(verb_code, "value", "") if verb_code is not None else ""
        lexemes.append(
            TokenLexeme(
                text=str(getattr(token, "text", "") or ""),
                lemma=str(getattr(token, "lemma", "") or ""),
                category=str(getattr(token, "cat", "") or ""),
                gram_case=str(getattr(token, "case", "") or ""),
                semantic_code=str(semantic_code or ""),
                char_start=int(getattr(token, "char_start", -1)),
                char_end=int(getattr(token, "char_end", -1)),
            )
        )
    return TokenTape(source_text=text, lexemes=tuple(lexemes))


def _build_finland_frontend_diagnostics(
    *,
    diagnostics: list[str],
    residuals: list,
    parse_error: str | None,
    internal_error_phase: str | None,
    lowering_diagnostics: tuple,
) -> tuple[FrontendDiagnostic, ...]:
    out: list[FrontendDiagnostic] = []
    if parse_error and internal_error_phase:
        out.append(
            FrontendDiagnostic(
                diagnostic_id=f"fi-johtolause-{internal_error_phase}-internal-error",
                jurisdiction="fi",
                frontend=FINLAND_JOHTOLAUSE_FRONTEND_ID,
                phase=internal_error_phase,
                severity="bug",
                rule_id=f"fi.johtolause.{internal_error_phase}.internal_error.v1",
                message=parse_error,
                blocking=True,
                strict_disposition="block",
                quirks_disposition="record",
                safe_default="do_not_promote_failed_parse_to_authority",
                forbidden_shortcuts=(
                    "swallow_internal_parser_bug",
                    "derive_replay_from_failed_phase",
                ),
                detail={
                    "human_diagnostics": tuple(diagnostics),
                },
            )
        )
    if residuals:
        residual_kinds = tuple(
            str(entry.get("kind", "unknown"))
            for entry in residuals
            if isinstance(entry, dict)
        )
        out.append(
            FrontendDiagnostic(
                diagnostic_id="fi-johtolause-residuals-present",
                jurisdiction="fi",
                frontend=FINLAND_JOHTOLAUSE_FRONTEND_ID,
                phase="residual_collection",
                severity="warning",
                rule_id="fi.johtolause.residuals_present.v1",
                message="Finland clause parse produced residual material.",
                blocking=False,
                strict_disposition="record",
                quirks_disposition="record",
                safe_default="record_residuals_without_replay_authority",
                forbidden_shortcuts=("drop_unconsumed_or_unresolved_parse_material",),
                detail={
                    "residual_kinds": residual_kinds,
                    "residual_count": len(residuals),
                },
            )
        )
    if lowering_diagnostics:
        out.append(
            FrontendDiagnostic(
                diagnostic_id="fi-johtolause-lowering-diagnostics",
                jurisdiction="fi",
                frontend=FINLAND_JOHTOLAUSE_FRONTEND_ID,
                phase="clause_ast_lowering",
                severity="warning",
                rule_id="fi.johtolause.lowering_diagnostics_present.v1",
                message="ClauseAST lowering emitted typed diagnostics.",
                blocking=False,
                strict_disposition="record",
                quirks_disposition="record",
                safe_default="record_lowering_diagnostics_without_replay_authority",
                forbidden_shortcuts=("drop_unlowerable_surface_nodes",),
                detail={
                    "lowering_diagnostic_count": len(lowering_diagnostics),
                },
            )
        )
    return tuple(out)


def _derive_parsed_ops_from_ast(clause_ast: ClauseAST) -> list[ParsedOp]:
    """Derive ParsedOps from ClauseAST through the Finland bridge.

    Walks the AST directly, using VerbGroup.verb (action string) to recover
    the Finnish verb code.  This preserves the governing verb from the
    original parse, even when node-level actions diverge (e.g. heading_replace
    under an 'insert' verb group stays verb='L').
    """
    from lawvm.core.clause_ast import (
        ScopedBlock,
        RefAmend,
        LabelAmend,
        MetaClause,
        TextAmend,
    )
    from lawvm.core.semantic_types import StructuralAction

    ops: list[ParsedOp] = []

    def _node_to_ops(
        node: object,
        verb: str,
        scope_chapter: str,
        scope_part: str,
    ) -> None:
        if isinstance(node, ScopedBlock):
            s_chapter = scope_chapter
            s_part = scope_part
            for kind, label in node.scope.path:
                if kind == "chapter":
                    s_chapter = label
                elif kind == "part":
                    s_part = label
            for child in node.children:
                _node_to_ops(child, verb, s_chapter, s_part)
            return

        if isinstance(node, MetaClause):
            return  # No ParsedOp equivalent

        if isinstance(node, TextAmend):
            return  # No ParsedOp equivalent

        if not isinstance(node, (RefAmend, LabelAmend)):
            return  # Unknown node type — skip

        # RefAmend or LabelAmend — extract target info
        target = node.target
        path_dict: dict[str, str] = {}
        for kind, label in target.path:
            path_dict[kind] = label

        leaf_kind = target.leaf_kind() if target.path else ""
        kind = TargetKind.for_leaf_kind(leaf_kind)

        part = path_dict.get("part", "") or scope_part
        chapter = path_dict.get("chapter", "") or scope_chapter
        number = ""
        momentti = 0
        item = ""

        # Map target.special to facet (keep as FacetKind enum)
        facet = target.special if target.special else None

        if kind is TargetKind.SECTION:
            number = path_dict.get("section", "")
            momentti = int(path_dict.get("subsection", "0") or "0")
            item = path_dict.get("item", "")
        elif kind is TargetKind.CHAPTER:
            number = path_dict.get("chapter", "")
            chapter = ""  # chapter-kind ops don't carry chapter context
        elif kind is TargetKind.PART:
            number = path_dict.get("part", "")
            part = ""  # part-kind ops don't carry part context
        elif kind is TargetKind.NIMIKE:
            number = path_dict.get("nimike", "")
        elif kind is TargetKind.APPENDIX:
            number = path_dict.get("appendix", "")

        # Renumber destination
        renumber_dest = ""
        renumber_dest_chapter = ""
        renumber_dest_part = ""
        move_clause_target_unit_kind: Literal["section", "chapter", "part"] | None = None
        if isinstance(node, LabelAmend) and node.destination is not None:
            dest_dict: dict[str, str] = {}
            for dk, dl in node.destination.path:
                dest_dict[dk] = dl
            renumber_dest = node.destination.leaf_label() if node.destination.path else ""
            renumber_dest_chapter = dest_dict.get("chapter", "")
            renumber_dest_part = dest_dict.get("part", "")
            if node.action is not LabelAction.HEADING_REPLACE:
                move_clause_target_unit_kind = infer_move_clause_target_unit_kind(node.destination)
                if kind is TargetKind.SECTION and move_clause_target_unit_kind == "chapter" and renumber_dest_chapter:
                    chapter = renumber_dest_chapter
                elif kind is TargetKind.SECTION and move_clause_target_unit_kind == "part" and renumber_dest_part:
                    part = renumber_dest_part
        elif isinstance(node, LabelAmend) and node.new_label and node.action == LabelAction.RENUMBER:
            renumber_dest = node.new_label

        notes = tuple(node.notes) if node.notes else ()

        # ClauseAST amendment nodes carry explicit witness/source-token fields.
        _source_tokens = node.source_tokens
        _witness_rule_id = node.witness_rule_id
        _witness = None
        if _witness_rule_id is not None:
            from lawvm.core.parse_witness import ParseWitness

            _witness = ParseWitness(
                rule_id=_witness_rule_id,
                source_span=_source_tokens,
            )

        op = ParsedOp(
            verb=verb,
            kind=kind.value,
            chapter=chapter,
            number=number,
            momentti=momentti,
            item=item,
            raw="",
            facet=facet,
            part=part,
            notes=notes,
            source_tokens=_source_tokens,
            renumber_dest=renumber_dest,
            renumber_dest_chapter=renumber_dest_chapter,
            renumber_dest_part=renumber_dest_part,
            witness=_witness,
            move_clause_target_unit_kind=move_clause_target_unit_kind,
        )
        op.raw = op.code()
        ops.append(op)

    for vg in clause_ast.verb_groups:
        # VerbGroup.verb is a shared StructuralAction enum.
        if isinstance(vg.verb, StructuralAction):
            verb_map = {
                StructuralAction.REPLACE: "M",
                StructuralAction.REPEAL: "K",
                StructuralAction.INSERT: "L",
                StructuralAction.RENUMBER: "S",
                StructuralAction.META: "M",
            }
            verb = verb_map.get(vg.verb, "M")
        else:
            verb = str(vg.verb)
        for node in vg.nodes:
            _node_to_ops(node, verb, "", "")

    return ops


# ═══════════════════════════════════════════════════════════════════════
# Text amend extraction (regex-based, like meta_parse)
# ═══════════════════════════════════════════════════════════════════════

_TEXT_AMEND_QUOTE = '"\u201c\u201d\u2018\u2019\u00ab\u00bb'

_TEXT_AMEND_RE = re.compile(
    r"(?:"
    r"(?P<sec>\d+\s*[a-z]?)\s*\u00a7"
    r"(?:"
    r":n\s+(?P<mom>\d+)\s+momenti(?:ssa|n(?:\s+(?P<kohta>\d+)\s+kohda(?:ssa|n))?)"
    r"|:ss[a\u00e4]"
    r"|:n"
    r")"
    r"\s+)?"
    r"sanat?\s+"
    r"[" + _TEXT_AMEND_QUOTE + r"]"
    r"(?P<old>[^" + _TEXT_AMEND_QUOTE + r"]+)"
    r"[" + _TEXT_AMEND_QUOTE + r"]"
    r"\s+korvataan\s+(?:sanalla|sanoilla)\s+"
    r"[" + _TEXT_AMEND_QUOTE + r"]"
    r"(?P<new>[^" + _TEXT_AMEND_QUOTE + r"]+)"
    r"[" + _TEXT_AMEND_QUOTE + r"]",
    re.IGNORECASE,
)


def _extract_text_amend_clauses(text: str) -> list:
    """Extract text amendment clauses from johtolause text."""
    from lawvm.finland.johtolause.surface_model import (
        SurfaceTargetRef,
        SurfaceTextAmend,
        SurfaceSubRef,
        SurfaceWitness,
        TargetKind,
    )

    if not text:
        return []
    results: list = []
    for m in _TEXT_AMEND_RE.finditer(text):
        sec = re.sub(r"\s+", "", (m.group("sec") or "").strip())  # "5 a" → "5a"
        mom_str = m.group("mom")
        kohta_str = m.group("kohta")
        old_text = m.group("old").strip()
        new_text = m.group("new").strip()
        target = None
        if sec:
            sub_refs: tuple = ()
            if mom_str and mom_str.isdigit():
                item = kohta_str if kohta_str else ""
                sub_refs = (SurfaceSubRef(momentti=int(mom_str), item=item),)
            target = SurfaceTargetRef(
                kind=TargetKind.SECTION,
                label=sec,
                sub_refs=sub_refs,
                witness=SurfaceWitness(rule_id="fi.text_amend_target"),
            )
        results.append(
            SurfaceTextAmend(
                target=target,
                old_text=old_text,
                new_text=new_text,
                witness=SurfaceWitness(rule_id="fi.text_amend_sana"),
            )
        )
    return results


def _inject_meta_nodes(
    surface_clause: _SurfaceClauseType,
    meta_nodes: Sequence[_SurfaceNodeType],
) -> _SurfaceClauseType:
    from lawvm.finland.johtolause.surface_model import (
        SurfaceClause as _SC,
        SurfaceVerbGroup as _SVG,
        VerbKind as _VK,
    )

    meta_tuple = tuple(meta_nodes)
    vgs = surface_clause.verb_groups
    if vgs:
        last_vg = vgs[-1]
        new_last = _SVG(verb=last_vg.verb, nodes=last_vg.nodes + meta_tuple)
        new_vgs = vgs[:-1] + (new_last,)
    else:
        new_vgs = (_SVG(verb=_VK.MUUTTAA, nodes=meta_tuple),)
    return _SC(
        verb_groups=new_vgs,
        source_text=surface_clause.source_text,
        consumed_count=surface_clause.consumed_count,
    )


# ═══════════════════════════════════════════════════════════════════════
# Feature derivation
# ═══════════════════════════════════════════════════════════════════════


def derive_features(text: str, ops: list[ParsedOp]) -> frozenset[str]:
    """Derive feature tags from raw johtolause text and its ParsedOps.

    Returns a frozenset of string feature tags compatible with the curated
    test feature vocabulary.
    """
    from lawvm.finland.johtolause.lexer import tokenize

    raw_tokens = tokenize(text)
    raw_cats = frozenset(t.cat for t in raw_tokens)
    raw_text = " ".join((t.text or "").lower() for t in raw_tokens)

    features: set[str] = set()
    if "NUMERO" in raw_cats:
        features.add("renumber")
    if re.search(r"mainit(?:un|tu)\s+pykäl(?:än|ä)", raw_text):
        features.add("backref_singular")
    if re.search(r"mainitt(?:ujen|ut)\s+pykäl(?:ien|ät)", raw_text):
        features.add("backref_plural")
    for op in ops:
        verb_names = {"M": "verb_muuttaa", "K": "verb_kumota", "L": "verb_lisata", "S": "verb_siirtaa"}
        if op.verb in verb_names:
            features.add(verb_names[op.verb])
        if op.typed_kind is TargetKind.SECTION:
            features.add("section_ref")
        elif op.typed_kind is TargetKind.CHAPTER:
            features.add("chapter_ref")
        elif op.typed_kind is TargetKind.PART:
            features.add("part_ref")
        elif op.typed_kind is TargetKind.APPENDIX:
            features.add("appendix_ref")
        elif op.typed_kind is TargetKind.NIMIKE:
            features.add("nimike_ref")
        if op.momentti:
            features.add("sub_ref_momentti")
            features.add("sub_ref")
        if op.item:
            features.add("sub_ref_kohta")
            features.add("sub_ref")
        if op.facet == FacetKind.HEADING:
            features.add("sub_ref_otsikko")
            features.add("otsikko")
            features.add("sub_ref")
        elif op.facet == FacetKind.INTRO:
            features.add("sub_ref_johd")
            features.add("johdantokappale")
            features.add("sub_ref")
        if op.chapter:
            features.add("chapter_ctx_propagation")
        if op.part:
            features.add("part_ctx")

    if len(set(op.verb for op in ops)) > 1:
        features.add("multi_verb_group")
    if len(ops) > 1:
        features.add("conj_target_list")
    if "CITE" in raw_cats:
        features.add("split_citation")

    return frozenset(features)

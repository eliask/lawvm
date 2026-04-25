"""diagnostics �� Residual reporting, coverage metrics, and parse explanations.

Reads the authoritative parse result rather than re-running parser internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.core.clause_ast import ClauseAST

if TYPE_CHECKING:
    from lawvm.finland.johtolause.api import ClauseParseResult


def _has_internal_error(parse_result: "ClauseParseResult | None") -> bool:
    """Return True if any diagnostic in parse_result signals an internal crash."""
    if parse_result is None:
        return False
    return any(d.startswith("internal_error:") for d in parse_result.diagnostics)


@dataclass
class ParseDiagnostic:
    """Diagnostic output from extract_ops_diagnostic."""

    ops: list[ParsedOp]
    raw_tokens: list[Token]
    filtered_tokens: list[Token]
    consumed_count: int  # how many filtered tokens the grammar consumed
    residual: list[Token]  # unconsumed tokens after parse
    has_verb: bool  # did the filtered stream contain any VERB token?
    clause_ast: Optional[ClauseAST] = None
    parse_result: Optional["ClauseParseResult"] = field(default=None, repr=False)

    @property
    def coverage(self) -> float:
        """Fraction of filtered tokens consumed (0.0-1.0)."""
        if not self.filtered_tokens:
            return 1.0
        return self.consumed_count / len(self.filtered_tokens)

    @property
    def failure_reason(self) -> str:
        """Heuristic classification of why extraction may have empty/incomplete."""
        if self.ops:
            if self.residual:
                cats = [t.cat for t in self.residual]
                if "VERB" in cats:
                    return "PARTIAL_VERB_GROUP"
                return "PARTIAL_RESIDUAL"
            return "OK"
        # Check for internal crash before heuristic grammar classification.
        # An internal error in the resolver or lowerer must not be reported as a
        # grammar problem — the grammar may have matched correctly.
        if _has_internal_error(self.parse_result):
            return "INTERNAL_ERROR"
        if not self.has_verb:
            return "NO_VERB"
        filtered_cats = [t.cat for t in self.filtered_tokens]
        if "NUM" not in filtered_cats and "LIITE" not in filtered_cats and "NIMIKE" not in filtered_cats:
            return "NO_STRUCTURAL_TARGET"
        return "GRAMMAR_MISMATCH"


def extract_ops_diagnostic(text: str) -> ParseDiagnostic:
    """Extract ops with full diagnostic information.

    Uses the authoritative parse pipeline — no shadow re-parse.
    Consumed count comes directly from the parser's SurfaceClause.
    Ops are derived from the ClauseAST via the canonical path.
    """
    from lawvm.finland.johtolause.scan import apply_annotations
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.api import parse_clause

    raw_tokens = tokenize(text)
    filtered_tokens = apply_annotations(raw_tokens)
    has_verb = any(t.cat == "VERB" for t in filtered_tokens)

    # Use the canonical pipeline for ops and AST
    result = parse_clause(text)
    ops = result.parsed_ops
    ast = result.clause_ast

    # Consumed count from the SurfaceClause
    consumed = 0
    if result.surface_clause is not None:
        consumed = result.surface_clause.consumed_count

    residual = filtered_tokens[consumed:]

    return ParseDiagnostic(
        ops=ops,
        raw_tokens=raw_tokens,
        filtered_tokens=filtered_tokens,
        consumed_count=consumed,
        residual=residual,
        has_verb=has_verb,
        clause_ast=ast,
        parse_result=result,
    )

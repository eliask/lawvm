"""peg3 — DEPRECATION FACADE for the Finland amendment clause parser.

All implementation lives in focused modules.  This file re-exports the
public API so existing ``from lawvm.finland.johtolause.peg3 import X``
import sites continue to work.  New code should import from the
canonical modules directly:

  lexicon.py       — Token type, closed vocabulary
  lexer.py         — tokenize(), witness_char_span()
  surface_parse.py — Stream, SubRef, VerbGroupContext, parse()
  diagnostics.py   — ParseDiagnostic, extract_ops_diagnostic()
  compat.py        — parse_clause(), ClauseParseResult, derive_features()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# ---- lexicon ----
from lawvm.finland.johtolause.lexicon import Token  # noqa: F401

# ---- lexer ----
from lawvm.finland.johtolause.lexer import (  # noqa: F401
    tokenize,
    witness_char_span,
)

# ---- surface_parse ----
from lawvm.finland.johtolause.surface_parse import (  # noqa: F401
    Stream,
    SubRef,
    VerbGroupContext,
    _skip_prov_span,
)

# ---- diagnostics ----
from lawvm.finland.johtolause.diagnostics import (  # noqa: F401
    ParseDiagnostic,
    extract_ops_diagnostic,
)

# ---- compat (canonical API) ----
from lawvm.finland.johtolause.api import (  # noqa: F401
    ClauseParseResult,
    parse_clause,
    derive_features,
)

if TYPE_CHECKING:
    from lawvm.finland.johtolause.types import ParsedOp


def parse_to_ops(tokens: list[Token]) -> list[ParsedOp]:
    """Parse tokens and return flat ParsedOp list via the canonical pipeline.

    Backward-compatibility wrapper.  New callers should use
    api.parse_clause() instead.

    Path:
        tokens -> surface_parse.parse() -> SurfaceClause
        -> resolve_surface_clause() -> ResolvedSurfaceClause
        -> lower_to_clause_ast() -> ClauseAST
        -> _derive_parsed_ops_from_ast() -> list[ParsedOp]
    """
    from lawvm.finland.johtolause.surface_parse import parse as _parse
    from lawvm.finland.johtolause.surface_resolve import resolve_surface_clause
    from lawvm.finland.johtolause.lower_clause_ast import lower_to_clause_ast
    from lawvm.finland.johtolause.api import _derive_parsed_ops_from_ast
    from lawvm.core.clause_ast import ClauseAST as _ClauseAST

    surface_clause = _parse(tokens)
    try:
        resolved = resolve_surface_clause(surface_clause)
    except Exception:
        return []
    try:
        clause_ast = lower_to_clause_ast(resolved)
    except Exception:
        clause_ast = _ClauseAST(verb_groups=(), source_text="")
    return _derive_parsed_ops_from_ast(clause_ast)


# backward compat: peg3.parse() returns list[ParsedOp]
parse = parse_to_ops  # noqa: F811

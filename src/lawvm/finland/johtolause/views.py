"""views — StructuralTokenView builder and Token-list bridge.

This module owns:
  - build_structural_view(): TokenTape + AnnotationSet → StructuralTokenView
  - structural_view_to_tokens(): StructuralTokenView → list[Token]

Architecture:
    The StructuralTokenView is the parser's actual input.  It is a view
    over TokenTape + AnnotationSet that hides annotation-covered spans and
    optionally replaces some span kinds with sentinel macro tokens.

    The Token-list bridge (structural_view_to_tokens) converts the view
    back to the list[Token] format that the existing surface_parse.py
    parser consumes.  This enables incremental migration: the parser
    continues to work on list[Token], but the upstream pipeline produces
    Phase 2 types.  As parser rules are migrated to consume the view
    directly, the bridge becomes unnecessary.

    Macro token lifting: annotation kinds listed in _SENTINEL_KINDS
    produce a single sentinel Token at the annotation's start position
    in the output.  Annotation kinds listed in _HIDDEN_KINDS produce
    no output (pure removal).  This matches the behavior of scan.py's
    structural_view() method.
"""

from __future__ import annotations

from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.token_model import (
    AnnotationSet,
    StructuralTokenView,
    TokenTape,
)
from lawvm.finland.source_verb import SourceVerb


# ---------------------------------------------------------------------------
# Sentinel token configuration — built from sentinels.py (single source of truth)
# ---------------------------------------------------------------------------

from lawvm.finland.johtolause.sentinels import _SENTINEL_SPECS as _ALL_SENTINEL_SPECS

_SENTINEL_SPECS: dict[str, tuple[str, str, str]] = {
    spec.kind: (spec.text, spec.lemma, spec.cat) for spec in _ALL_SENTINEL_SPECS
}

# Annotation kinds that produce no output token (pure removal).
_HIDDEN_KINDS: frozenset[str] = frozenset({"qualifier", "punct"})

# Formal title suffix uses the citation_span sentinel
# (sentinel_cat is stored in detail["sentinel_cat"])


def _sentinel_token_for(kind: str, detail: dict | None = None) -> Token | None:
    """Create a sentinel Token for an annotation kind, or None for hidden kinds."""
    if kind in _HIDDEN_KINDS:
        return None

    # Check detail for sentinel_cat override (e.g. formal_title_suffix
    # uses CITATION_SPAN sentinel)
    if detail and "sentinel_cat" in detail:
        sentinel_cat = detail["sentinel_cat"]
        # Look up by sentinel_cat rather than kind
        for _kind, spec in _SENTINEL_SPECS.items():
            if spec[2] == sentinel_cat:
                return Token(
                    text=spec[0],
                    lemma=spec[1],
                    cat=spec[2],
                    case="",
                    verb_code=None,
                )
        # Empty sentinel_cat means pure removal (hidden)
        if not sentinel_cat:
            return None

    spec = _SENTINEL_SPECS.get(kind)
    if spec is not None:
        return Token(
            text=spec[0],
            lemma=spec[1],
            cat=spec[2],
            case="",
            verb_code=None,
        )
    return None


# ---------------------------------------------------------------------------
# StructuralTokenView builder
# ---------------------------------------------------------------------------


def build_structural_view(
    tape: TokenTape,
    annotation_set: AnnotationSet,
) -> StructuralTokenView:
    """Build a StructuralTokenView from a TokenTape and AnnotationSet.

    Tokens covered by annotations are hidden.  Annotations that produce
    sentinel tokens contribute a virtual position (using the annotation's
    start index) to the visible_indices.  Annotations with no sentinel
    (pure removal kinds) hide their tokens entirely.

    When multiple annotations overlap, the outermost (longest) span wins.
    This matches scan.py's structural_view() behavior.
    """
    n = len(tape)
    if n == 0:
        return StructuralTokenView(
            tape=tape,
            annotations=annotation_set,
            visible_indices=(),
        )

    # Build per-position annotation mask (outermost-wins for overlaps).
    # The mask maps each tape index to the annotation covering it (if any).
    # Larger spans take precedence (same as scan.py._build_covered_mask).
    from lawvm.finland.johtolause.token_model import Annotation as TmAnnotation

    covered: list[TmAnnotation | None] = [None] * n
    sorted_anns = sorted(
        annotation_set.annotations,
        key=lambda a: -(a.end - a.start),  # longest first
    )
    for ann in sorted_anns:
        for i in range(ann.start, min(ann.end, n)):
            if covered[i] is None:
                covered[i] = ann

    # Walk the tape and build visible_indices.
    # For each annotation span: emit sentinel index at span start (if the
    # kind produces a sentinel), then skip to span end.
    # For uncovered positions: include the tape index directly.
    visible: list[int] = []
    i = 0
    while i < n:
        ann = covered[i]
        if ann is None:
            visible.append(i)
            i += 1
        else:
            # Annotation span: check if it produces a sentinel
            sentinel_detail = dict(ann.detail) if ann.detail else None
            sentinel = _sentinel_token_for(ann.kind, sentinel_detail)
            if sentinel is not None:
                # Use the annotation's start index as a marker.
                # The actual sentinel token is NOT in the tape — it's a
                # virtual token.  We record the annotation start index
                # as a sentinel marker; consumers of the view that need
                # the actual Token list use structural_view_to_tokens().
                visible.append(i)
            i = ann.end

    return StructuralTokenView(
        tape=tape,
        annotations=annotation_set,
        visible_indices=tuple(visible),
    )


# ---------------------------------------------------------------------------
# Token-list bridge (for backward compatibility with surface_parse.py)
# ---------------------------------------------------------------------------


def structural_view_to_tokens(
    tape: TokenTape,
    annotation_set: AnnotationSet,
) -> list[Token]:
    """Convert TokenTape + AnnotationSet to the list[Token] the parser expects.

    This is the backward-compatible bridge: it produces exactly the same
    token list as scan.py::apply_annotations().  Annotation-covered tokens
    are replaced by sentinel Tokens (or removed entirely for hidden kinds).

    The output is directly consumable by surface_parse.parse().
    """
    n = len(tape)
    if n == 0:
        return []

    # Build per-position annotation mask (same as build_structural_view)
    from lawvm.finland.johtolause.token_model import Annotation as TmAnnotation

    covered: list[TmAnnotation | None] = [None] * n
    sorted_anns = sorted(
        annotation_set.annotations,
        key=lambda a: -(a.end - a.start),
    )
    for ann in sorted_anns:
        for i in range(ann.start, min(ann.end, n)):
            if covered[i] is None:
                covered[i] = ann

    # Walk tape and build Token list
    result: list[Token] = []
    i = 0
    while i < n:
        ann = covered[i]
        if ann is None:
            lex = tape.lexemes[i]
            result.append(
                Token(
                    text=lex.text,
                    lemma=lex.lemma,
                    cat=lex.category,
                    case=lex.gram_case,
                    verb_code=lex.verb_code if isinstance(lex.verb_code, SourceVerb) else None,
                    char_start=lex.char_start,
                    char_end=lex.char_end,
                )
            )
            i += 1
        else:
            sentinel_detail = dict(ann.detail) if ann.detail else None
            sentinel = _sentinel_token_for(ann.kind, sentinel_detail)
            if sentinel is not None:
                result.append(sentinel)
            i = ann.end

    return result

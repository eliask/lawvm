"""TokenTape + AnnotationSet + StructuralTokenView -- Phase 2 data model.

These are the pure type definitions for the Finland clause compiler
architecture described in PRO_FI_PEG_VPRI_2026-04-07.md section 3.1.

Design:
    TokenTape is the immutable lexical fact surface.  The parser never
    mutates or skips this tape directly.

    AnnotationSet carries source-local non-structural span recognitions
    (citations, provenance, heading placement, etc.) as overlays on the
    tape.

    StructuralTokenView is the parser's actual input -- a view over
    TokenTape + AnnotationSet that hides annotation-covered spans and
    optionally lifts some span kinds into macro tokens.  The parser
    never sees raw sentinel categories.

    ClauseParseResult is the public parse output: clause AST + full
    provenance (tape, annotations, residuals).

These types are independent of the current peg3 Token / scan.py
Annotation types.  Migration will bridge the two representations;
this module carries no parsing logic.

Token categories in the current peg3 lexicon (for reference, not imported):
    Structural:  VERB NUM PYKALA LUKU OSA MOMENTTI KOHTA LIITE NIMIKE
    Qualifier:   OTSIKKO JOHD UUSI ALAKOHTA LANGQUAL TEMPORAL
    Connective:  CONJ COMMA DASH PUNCT
    Document:    DOC END
    Reference:   PROV REINST TILALLE JOLLOIN BACKREF EDELLA NUMERO
    Annotation:  CITATION_SPAN PROVENANCE_SPAN STATUTE_NAME_SPAN
                 REINST_SPAN END_SENTINEL_SPAN JOLLOIN_MOVE VALIOTSIKKO
    Residual:    WORD LETTER CITE

Grammatical cases in the current peg3 lexicon:
    NOM  -- nominative
    GEN  -- genitive
    ILL  -- illative
    (empty string when case is not applicable)
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType as _MappingProxyType
from typing import Any, Mapping

from lawvm.finland.source_verb import SourceVerb

# Singleton empty mapping used as default for Annotation.detail to avoid
# mutable default argument pitfalls.
_EMPTY_DETAIL: Mapping[str, Any] = _MappingProxyType({})


# ---------------------------------------------------------------------------
# TokenTape -- immutable lexical fact surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Lexeme:
    """One classified token in the amendment clause stream.

    Mirrors the information in the current peg3.Token but uses names
    aligned with the Phase 2 architecture.

    Attributes:
        text:       Original surface form.
        lemma:      Canonical form (lowercase).
        category:   Token category string (see module docstring for inventory).
        gram_case:  Grammatical case: "NOM", "GEN", "ILL", or "" when
                    case is not applicable.
        verb_code:  Verb classification: SourceVerb enum member for verb tokens,
                    empty string for non-verb tokens.
        char_start: Character offset in the normalized input string (inclusive).
                    -1 when not tracked (legacy/sentinel callers).
        char_end:   Character offset in the normalized input string (exclusive).
                    -1 when not tracked.
    """

    text: str
    lemma: str
    category: str
    gram_case: str
    verb_code: "SourceVerb | str | None"
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class TokenTape:
    """Immutable lexical record of a Finnish amendment clause.

    The parser does NOT mutate or skip this tape.  All filtering and
    span-hiding happens through AnnotationSet + StructuralTokenView.

    Attributes:
        source_text: The normalized input text that was tokenized.
        lexemes:     Ordered tuple of classified tokens with stable offsets.
    """

    source_text: str
    lexemes: tuple[Lexeme, ...]

    def __len__(self) -> int:
        return len(self.lexemes)


# ---------------------------------------------------------------------------
# AnnotationSet -- span overlays on the token tape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Annotation:
    """A recognized non-structural span overlay on the token tape.

    Attributes:
        kind:   Classification of the span.  Expected kinds:
                "citation_span", "provenance_span", "statute_name_span",
                "heading_placement", "move_tail", "renumber_tail",
                "meta_effect", "reinstatement", "end_sentinel",
                "jolloin", "qualifier", "formal_title_suffix".
        start:  Index into TokenTape.lexemes (inclusive).
        end:    Index into TokenTape.lexemes (exclusive).
        detail: Optional structured data (e.g. citation text, renumber
                pairs).  Immutable mapping; callers should pass a
                types.MappingProxyType or plain dict (frozen by convention).
    """

    kind: str
    start: int
    end: int
    detail: Mapping[str, Any] = _EMPTY_DETAIL

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError(f"Annotation start must be >= 0, got {self.start}")
        if self.end < self.start:
            raise ValueError(f"Annotation end ({self.end}) must be >= start ({self.start})")

    def __len__(self) -> int:
        return self.end - self.start

    def covers(self, index: int) -> bool:
        """Return True if *index* falls within this annotation's span."""
        return self.start <= index < self.end


@dataclass(frozen=True, slots=True)
class AnnotationSet:
    """Collection of span annotations over a TokenTape.

    Annotations may overlap (e.g. nested provenance + citation).
    Ordering is not significant -- consumers should sort as needed.

    Attributes:
        annotations: Ordered tuple of Annotation objects.
    """

    annotations: tuple[Annotation, ...]

    def __len__(self) -> int:
        return len(self.annotations)

    def covering(self, index: int) -> tuple[Annotation, ...]:
        """Return all annotations whose span covers *index*."""
        return tuple(a for a in self.annotations if a.covers(index))

    def of_kind(self, kind: str) -> tuple[Annotation, ...]:
        """Return all annotations matching *kind*."""
        return tuple(a for a in self.annotations if a.kind == kind)


# ---------------------------------------------------------------------------
# StructuralTokenView -- parser's actual input
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructuralTokenView:
    """Parser's actual input -- a view over TokenTape + AnnotationSet.

    Hides annotation-covered spans, optionally lifts some span kinds
    into macro tokens.  The parser never sees raw sentinel categories.

    Attributes:
        tape:             The underlying immutable token tape.
        annotations:      The annotation set used to build this view.
        visible_indices:  Indices into tape.lexemes that the parser
                          should see.  This tuple defines the parser's
                          token stream: ``tape.lexemes[i]`` for each
                          ``i`` in ``visible_indices``.
    """

    tape: TokenTape
    annotations: AnnotationSet
    visible_indices: tuple[int, ...]

    def __len__(self) -> int:
        return len(self.visible_indices)

    def lexeme_at(self, view_pos: int) -> Lexeme:
        """Return the Lexeme at *view_pos* in the structural view."""
        return self.tape.lexemes[self.visible_indices[view_pos]]

    def tape_index(self, view_pos: int) -> int:
        """Return the tape index corresponding to *view_pos*."""
        return self.visible_indices[view_pos]


# ---------------------------------------------------------------------------
# Phase2ParseResult -- Phase 2 public parse output (frozen, with provenance)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Phase2ParseResult:
    """Phase 2 public parse output: clause AST + full provenance.

    Every parse always includes the token tape, annotations, and
    residual indices -- not as a diagnostic mode but as part of
    the epistemic plane.

    Attributes:
        clause_ast:     The parsed ClauseAST (or None if parse failed).
        token_tape:     The immutable lexical record.
        annotations:    Annotations recognized during scanning/annotation.
        surface_clause: The Finland-local surface parse tree before final
                        lowering (or None if not produced).
        residual:       Tuple of tape lexeme indices that were not consumed
                        by the parser.  Non-empty residual is normal for
                        clauses with unrecognized trailing material.
        meta_clauses:   Tuple of SurfaceMetaClause nodes extracted from the
                        johtolause text (commencement, expiry, transition,
                        delegation).  Empty tuple when no meta patterns match
                        or when parse_clause() is not the construction path.
                        Populated by parse_clause() in compat.py (Phase 7).
    """

    clause_ast: Any  # ClauseAST | None -- typed as Any to avoid circular import
    token_tape: TokenTape
    annotations: AnnotationSet
    surface_clause: Any  # SurfaceClause | None
    residual: tuple[int, ...]
    meta_clauses: tuple[Any, ...] = ()  # tuple[SurfaceMetaClause, ...]

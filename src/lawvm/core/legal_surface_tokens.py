"""Source-preserving token substrate for the Legal Surface Algebra (Phase 7).

Authoritative design: ``notes_internal/pro_on_fi_theory_grammar5.txt`` §D4
("minimal substrate now ... TokenTape becomes a populated view on
SourceSurfaceUnit; lens required_views migrates from raw_text to token_tape").

This module is the **universal substrate algebra**: jurisdiction-neutral frozen
types only. A :class:`TokenTape` is a lossless, source-preserving view over one
``SourceSurfaceUnit``'s ``raw_text``. Each :class:`Token` carries EXACT character
spans into that text, so a consumer can always recover the verbatim substring
(``raw_text[t.char_start:t.char_end] == t.text``).

No Finnish (or any single-jurisdiction) tokenization rules live here — the
category set is the shared closed vocabulary; the actual segmentation is done by
a jurisdiction tokenizer (e.g. ``lawvm.finland.legal_surface.tokenize``) that
emits these types.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple

#: Closed set of jurisdiction-neutral token categories. A tokenizer MUST emit
#: only these. Adding a category is a deliberate edit here, never a heuristic.
TOKEN_CATEGORIES: frozenset[str] = frozenset(
    {
        "word",  # a run of letter characters (jurisdiction defines the alphabet)
        "number",  # a numeric run (may include internal dots for dates/decimals)
        "section_mark",  # the section sign "§" on its own
        "colon_suffix",  # a "§:n" / "§:ssä"-style colon-attached case suffix
        "punct",  # punctuation that is not a dash (",", ";", ".", ":", "(", ...)
        "dash",  # hyphen / en-dash / em-dash
        "whitespace",  # a run of whitespace characters
    }
)


@dataclass(frozen=True, slots=True)
class Token:
    """One lossless lexical unit with an EXACT span into the source text.

    Attributes:
        text:        Verbatim substring; ``raw_text[char_start:char_end] == text``.
        char_start:  0-based inclusive character offset into the source text.
        char_end:    0-based exclusive character offset into the source text.
        normalized:  Casefolded ``text`` (``str.casefold``), the matching key for
                     case-insensitive lens vocabularies.
        category:    A member of :data:`TOKEN_CATEGORIES`.
    """

    text: str
    char_start: int
    char_end: int
    normalized: str
    category: str

    def __post_init__(self) -> None:
        if self.char_start < 0:
            raise ValueError("Token.char_start must be >= 0")
        if self.char_end < self.char_start:
            raise ValueError("Token.char_end must be >= char_start")
        if self.char_end - self.char_start != len(self.text):
            raise ValueError(
                "Token span length must equal len(text): "
                f"{self.char_end - self.char_start} != {len(self.text)}"
            )
        if self.category not in TOKEN_CATEGORIES:
            raise ValueError(f"unknown token category: {self.category!r}")
        if self.normalized != self.text.casefold():
            raise ValueError("Token.normalized must be text.casefold()")


@dataclass(frozen=True, slots=True)
class TokenTape:
    """A lossless, source-preserving token sequence over one source unit.

    Attributes:
        source_unit_id: The ``SourceSurfaceUnit.source_unit_id`` this tape views.
        text_hash:      Hash of the exact source text the tape was built over
                        (the tokenizer's anchor; lets a consumer detect drift).
        tokens:         Document-order tokens. Adjacent token spans are
                        non-overlapping and (when whitespace is emitted) cover
                        the source text contiguously.
    """

    source_unit_id: str
    text_hash: str
    tokens: Tuple[Token, ...]

    def __post_init__(self) -> None:
        prev_end = 0
        for tok in self.tokens:
            if tok.char_start < prev_end:
                raise ValueError(
                    "TokenTape tokens must be non-overlapping and in document "
                    f"order: token at {tok.char_start} starts before {prev_end}"
                )
            prev_end = tok.char_end


@dataclass(frozen=True, slots=True)
class ClauseSpan:
    """One clause span over a source unit's text, with its enclosing sentence.

    Jurisdiction-NEUTRAL carrier: just character offsets into the unit's
    ``raw_text`` plus a back-pointer to the index of the enclosing sentence in
    the same :class:`ClauseIndex`. The segmentation RULES (which characters/cues
    bound a clause) are a jurisdiction concern and live in the jurisdiction
    segmenter (e.g. ``lawvm.finland.legal_surface.clause_segment``); this type
    only stores the result.

    Attributes:
        char_start:      0-based inclusive offset into the unit text.
        char_end:        0-based exclusive offset into the unit text.
        sentence_index:  Index of the enclosing :class:`SentenceSpan` in the
                         owning :class:`ClauseIndex.sentences`.
        clause_kind:     A jurisdiction-supplied opaque label for WHY this clause
                         boundary was drawn (e.g. ``"sentence"``, ``"comma"``,
                         ``"subordinator:jos"``). Carrier only — never
                         interpreted by core.
    """

    char_start: int
    char_end: int
    sentence_index: int
    clause_kind: str

    def __post_init__(self) -> None:
        if self.char_start < 0:
            raise ValueError("ClauseSpan.char_start must be >= 0")
        if self.char_end < self.char_start:
            raise ValueError("ClauseSpan.char_end must be >= char_start")
        if self.sentence_index < 0:
            raise ValueError("ClauseSpan.sentence_index must be >= 0")


@dataclass(frozen=True, slots=True)
class SentenceSpan:
    """One sentence span over a source unit's text. Jurisdiction-NEUTRAL carrier.

    Attributes:
        char_start: 0-based inclusive offset into the unit text.
        char_end:   0-based exclusive offset into the unit text.
    """

    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        if self.char_start < 0:
            raise ValueError("SentenceSpan.char_start must be >= 0")
        if self.char_end < self.char_start:
            raise ValueError("SentenceSpan.char_end must be >= char_start")


#: Result of a span→clause query that lands on a clause/sentence boundary (a
#: span that crosses a boundary cannot be assigned a single clause). The query
#: returns this sentinel rather than silently bucketing into one side —
#: fail-loud, never guess.
class _Ambiguous:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "AMBIGUOUS"


AMBIGUOUS = _Ambiguous()


@dataclass(frozen=True, slots=True)
class ClauseIndex:
    """A deterministic, source-anchored clause/sentence index over one unit.

    Jurisdiction-NEUTRAL carrier + query harness. The jurisdiction segmenter
    produces the ``sentences`` and ``clauses`` (every clause's
    ``sentence_index`` points into ``sentences``); this type stores them and
    answers the span→clause / span→sentence queries the later attachment passes
    need.

    Invariants (enforced):
      * spans are in document order and non-overlapping within each kind;
      * every clause's ``sentence_index`` is a valid index into ``sentences``;
      * each clause lies within its sentence's bounds.

    Attributes:
        source_unit_id: The ``SourceSurfaceUnit.source_unit_id`` this indexes.
        text_hash:      Hash of the exact text the index was built over (drift
                        anchor; same convention as :class:`TokenTape`).
        sentences:      Document-order sentence spans.
        clauses:        Document-order clause spans (finer than sentences).
    """

    source_unit_id: str
    text_hash: str
    sentences: Tuple[SentenceSpan, ...]
    clauses: Tuple[ClauseSpan, ...]

    def __post_init__(self) -> None:
        prev_end = 0
        for s in self.sentences:
            if s.char_start < prev_end:
                raise ValueError(
                    "ClauseIndex.sentences must be non-overlapping and in "
                    f"document order: sentence at {s.char_start} starts before "
                    f"{prev_end}"
                )
            prev_end = s.char_end
        n_sentences = len(self.sentences)
        prev_end = 0
        for c in self.clauses:
            if c.char_start < prev_end:
                raise ValueError(
                    "ClauseIndex.clauses must be non-overlapping and in "
                    f"document order: clause at {c.char_start} starts before "
                    f"{prev_end}"
                )
            prev_end = c.char_end
            if not 0 <= c.sentence_index < n_sentences:
                raise ValueError(
                    "ClauseIndex clause.sentence_index out of range: "
                    f"{c.sentence_index} not in [0, {n_sentences})"
                )
            sent = self.sentences[c.sentence_index]
            if c.char_start < sent.char_start or c.char_end > sent.char_end:
                raise ValueError(
                    "ClauseIndex clause must lie within its sentence: clause "
                    f"[{c.char_start},{c.char_end}] not inside sentence "
                    f"[{sent.char_start},{sent.char_end}]"
                )

    def sentence_at(self, char_start: int, char_end: int) -> "SentenceSpan | _Ambiguous":
        """Return the enclosing :class:`SentenceSpan` for a char span.

        A span fully inside one sentence returns that sentence. A span that
        crosses a sentence boundary (or falls outside every sentence) returns
        :data:`AMBIGUOUS` — never silently bucketed to one side. An empty span
        (``char_start == char_end``) is located by its point position.
        """
        return self._locate(char_start, char_end, self.sentences)

    def clause_at(self, char_start: int, char_end: int) -> "ClauseSpan | _Ambiguous":
        """Return the enclosing :class:`ClauseSpan` for a char span.

        Same contract as :meth:`sentence_at`: a span that crosses a clause
        boundary (or lies outside every clause) returns :data:`AMBIGUOUS`.
        """
        return self._locate(char_start, char_end, self.clauses)

    @staticmethod
    def _locate(char_start, char_end, spans):
        if char_end < char_start:
            raise ValueError("query char_end must be >= char_start")
        # Empty span: locate by point. Non-empty: the span must be fully inside
        # exactly one span; if it straddles a boundary or matches none, AMBIGUOUS.
        hi = char_end if char_end > char_start else char_start + 1
        enclosing = None
        for sp in spans:
            # overlap test on [char_start, hi)
            if sp.char_start < hi and char_start < sp.char_end:
                if enclosing is not None:
                    return AMBIGUOUS  # overlaps >1 span → crosses a boundary
                if char_start < sp.char_start or char_end > sp.char_end:
                    return AMBIGUOUS  # not fully contained → crosses a boundary
                enclosing = sp
        if enclosing is None:
            return AMBIGUOUS
        return enclosing


@dataclass(frozen=True, slots=True)
class MorphAnnotation:
    """A reverse-morphology annotation on a single :class:`Token` of a tape.

    Token-NEUTRAL: this carries only an index into a :class:`TokenTape` and the
    lemma(s) a jurisdiction analyzer mapped that token's surface to. The set of
    lemmas is whatever the analyzer returned; ambiguity is surfaced (more than
    one lemma), never resolved here.

    Attributes:
        token_index: 0-based index into the annotated :class:`TokenTape.tokens`.
        lemmas:      The lemma(s) the token's surface inverts to. Non-empty: an
                     annotation only exists for a token that maps to >= 1 lemma.
        unique:      ``True`` iff exactly one lemma matched (an unambiguous head).
    """

    token_index: int
    lemmas: Tuple[str, ...]
    unique: bool

    def __post_init__(self) -> None:
        if self.token_index < 0:
            raise ValueError("MorphAnnotation.token_index must be >= 0")
        if not self.lemmas:
            raise ValueError(
                "MorphAnnotation.lemmas must be non-empty (an annotation only "
                "exists for a token that maps to at least one lemma)"
            )
        if self.unique != (len(self.lemmas) == 1):
            raise ValueError(
                "MorphAnnotation.unique must equal (len(lemmas) == 1): "
                f"unique={self.unique} but len(lemmas)={len(self.lemmas)}"
            )


@dataclass(frozen=True, slots=True)
class MorphOverlay:
    """A SPARSE per-token reverse-morphology view over one :class:`TokenTape`.

    Maps ``token_index -> MorphAnnotation`` for the tokens whose surface inverts
    to a known lemma. The overlay is deliberately sparse: a token that is NOT
    present has no annotation, which a consumer MUST read as "unknown / outside
    the analyzed vocabulary" — never as "no lemma exists". The overlay is not a
    general lemmatizer; it covers only the closed vocabulary the analyzer knows.

    Attributes:
        source_unit_id: The unit whose tape this overlay annotates (drift anchor).
        text_hash:      The tape's ``text_hash`` the overlay was built over.
        annotations:    ``token_index -> MorphAnnotation`` for annotated tokens
                        ONLY (sparse). Every value's ``token_index`` equals its key.
    """

    source_unit_id: str
    text_hash: str
    annotations: Mapping[int, MorphAnnotation]

    def __post_init__(self) -> None:
        for index, ann in self.annotations.items():
            if index < 0:
                raise ValueError(
                    f"MorphOverlay annotation key must be >= 0, got {index}"
                )
            if ann.token_index != index:
                raise ValueError(
                    "MorphOverlay key must equal its annotation's token_index: "
                    f"key={index} but annotation.token_index={ann.token_index}"
                )

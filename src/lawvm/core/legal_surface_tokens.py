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


#: Closed taxonomy of structural segment kinds (the SegmentationGraph alphabet).
#: Jurisdiction-NEUTRAL: the segmenter MUST label every segment with one of these.
#: Adding a kind is a deliberate edit here, never a heuristic.
#:
#:   * ``heading``                — a section/chapter/subheading title line.
#:   * ``chapeau``                — the introductory stem governing a following
#:                                  enumerated list (the text before a ``:``).
#:   * ``list_item``              — one enumerated item under a chapeau; links to
#:                                  its governing chapeau (the inheritance edge).
#:   * ``definition_list``        — refinement of a chapeau whose items are
#:                                  definitional entries; carried as a ``role`` on
#:                                  the chapeau + its list_items, NOT a 4th kind on
#:                                  the items (so list inheritance stays uniform).
#:   * ``quoted_amendment_block`` — a quoted block of statutory text being
#:                                  inserted/substituted by an amendment act.
#:   * ``continuation``           — a segment continuing a prior one across a soft
#:                                  break (links to the segment it continues).
#:   * ``prose``                  — ordinary running paragraph text (the default).
#:   * ``residual``               — an EXPLICIT residual span: text owned but not
#:                                  interpreted (benign whitespace between lines,
#:                                  or a span whose structure the token tape does
#:                                  not carry). NEVER a silent drop — every
#:                                  residual records a ``residual_reason``.
SEGMENT_KINDS: frozenset[str] = frozenset(
    {
        "heading",
        "chapeau",
        "list_item",
        "definition_list",
        "quoted_amendment_block",
        "continuation",
        "prose",
        "residual",
    }
)


@dataclass(frozen=True, slots=True)
class StructuralSegment:
    """One structural segment over a source unit's text. Jurisdiction-NEUTRAL.

    A segment owns a contiguous char span ``[char_start, char_end)`` of the unit
    text and is labelled with a closed :data:`SEGMENT_KINDS` ``kind``. The
    segmentation RULES (which lines are headings / chapeaux / list items, how a
    list item is linked to its chapeau) are a jurisdiction concern and live in
    the jurisdiction segmenter (e.g. ``lawvm.finland.legal_surface.clause_segment``);
    this type only stores the result.

    Inheritance / continuation links are stored as an INDEX into the owning
    :class:`SegmentationGraph.segments` tuple (``parent_index``): a ``list_item``
    points at its governing ``chapeau``; a ``continuation`` points at the segment
    it continues. ``parent_index is None`` for a segment with no parent.

    Attributes:
        char_start:      0-based inclusive offset into the unit text.
        char_end:        0-based exclusive offset into the unit text.
        kind:            A member of :data:`SEGMENT_KINDS`.
        role:            A jurisdiction-supplied opaque refinement label (e.g.
                         ``"definition_list"`` tags a definitional chapeau/item,
                         ``"colon_chapeau"`` records WHY a chapeau boundary was
                         drawn). Carrier only — never interpreted by core. ``""``
                         when unrefined.
        parent_index:    Index into :class:`SegmentationGraph.segments` of the
                         governing chapeau (for ``list_item``) or the continued
                         segment (for ``continuation``); ``None`` otherwise.
        residual_reason: WHY this span is an explicit residual — REQUIRED and
                         non-empty for ``kind == "residual"`` (self-evidencing:
                         the reason names what was not interpreted), ``""`` for
                         every interpreted kind. This is the "no silent drop"
                         witness: a residual is owned and labelled, never hidden.
    """

    char_start: int
    char_end: int
    kind: str
    role: str = ""
    parent_index: "int | None" = None
    residual_reason: str = ""

    def __post_init__(self) -> None:
        if self.char_start < 0:
            raise ValueError("StructuralSegment.char_start must be >= 0")
        if self.char_end < self.char_start:
            raise ValueError("StructuralSegment.char_end must be >= char_start")
        if self.kind not in SEGMENT_KINDS:
            raise ValueError(f"unknown segment kind: {self.kind!r}")
        if self.parent_index is not None and self.parent_index < 0:
            raise ValueError("StructuralSegment.parent_index must be >= 0 or None")
        if self.kind == "residual":
            if not self.residual_reason:
                raise ValueError(
                    "a residual StructuralSegment MUST carry a non-empty "
                    "residual_reason (no silent drop: every residual names what "
                    "was left uninterpreted)"
                )
        elif self.residual_reason:
            raise ValueError(
                "residual_reason is only for kind=='residual'; an interpreted "
                f"segment ({self.kind!r}) must leave it empty"
            )


@dataclass(frozen=True, slots=True)
class SegmentationGraph:
    """A deterministic, source-anchored STRUCTURAL segmentation of one unit.

    Jurisdiction-NEUTRAL carrier + query harness, sitting one level ABOVE the
    sentence/clause :class:`ClauseIndex` in the SourceSyntaxGraph stack
    (``TokenTape → SegmentationGraph → SourceSyntaxGraph → LegalSurfaceGraph``).
    Where :class:`ClauseIndex` splits prose into sentences/clauses, this graph
    classifies the unit's whole text into the higher STRUCTURAL segments
    (headings, chapeaux, list items with chapeau inheritance, quoted amendment
    blocks, continuations, prose) plus EXPLICIT residual spans.

    THE KILLER INVARIANT — total token ownership / no silent drop (enforced in
    :meth:`__post_init__`):

        the segment spans, in document order, partition ``[0, text_len)``
        EXACTLY — contiguous, non-overlapping, no gaps. Every char of the unit
        is owned by exactly one segment; an uninterpreted span is owned as an
        explicit ``residual`` segment (with a reason), NEVER dropped.

    This is the structural analogue of :class:`TokenTape`'s contiguous-coverage
    totality, lifted to the segment layer. A consumer can therefore trust that no
    legal signal silently vanished between the tape and the segmentation.

    Attributes:
        source_unit_id: The ``SourceSurfaceUnit.source_unit_id`` this segments.
        text_hash:      Hash of the exact text segmented (drift anchor; same
                        convention as :class:`TokenTape` / :class:`ClauseIndex`).
        text_len:       ``len(raw_text)`` — the full span the segments partition.
        segments:       Document-order structural segments partitioning
                        ``[0, text_len)`` exactly.
    """

    source_unit_id: str
    text_hash: str
    text_len: int
    segments: Tuple[StructuralSegment, ...]

    def __post_init__(self) -> None:
        if self.text_len < 0:
            raise ValueError("SegmentationGraph.text_len must be >= 0")
        n = len(self.segments)
        cursor = 0
        for i, seg in enumerate(self.segments):
            # contiguous, in order, no gap, no overlap (exact partition)
            if seg.char_start != cursor:
                raise ValueError(
                    "SegmentationGraph segments must partition [0, text_len) "
                    "with NO gap/overlap (no silent drop): segment "
                    f"{i} starts at {seg.char_start} but expected {cursor}"
                )
            if seg.char_end < seg.char_start:
                raise ValueError(
                    f"SegmentationGraph segment {i} has char_end < char_start"
                )
            cursor = seg.char_end
            # parent/continuation index must point at an EARLIER segment
            if seg.parent_index is not None:
                if not 0 <= seg.parent_index < n:
                    raise ValueError(
                        "SegmentationGraph segment.parent_index out of range: "
                        f"{seg.parent_index} not in [0, {n})"
                    )
                if seg.parent_index >= i:
                    raise ValueError(
                        "SegmentationGraph parent/continuation link must point "
                        f"at an EARLIER segment: segment {i} -> {seg.parent_index}"
                    )
        if cursor != self.text_len:
            raise ValueError(
                "SegmentationGraph segments must cover the FULL text "
                f"[0, {self.text_len}) (no silent drop): coverage ends at "
                f"{cursor}, not {self.text_len}"
            )

    def segment_at(
        self, char_start: int, char_end: int
    ) -> "StructuralSegment | _Ambiguous":
        """Return the enclosing :class:`StructuralSegment` for a char span.

        Mirrors :meth:`ClauseIndex.clause_at`: a span fully inside one segment
        returns that segment; a span that crosses a segment boundary (or lies
        outside ``[0, text_len)``) returns :data:`AMBIGUOUS` — never silently
        bucketed to one side. An empty span is located by its point position.
        """
        if char_end < char_start:
            raise ValueError("query char_end must be >= char_start")
        hi = char_end if char_end > char_start else char_start + 1
        enclosing: StructuralSegment | None = None
        for seg in self.segments:
            if seg.char_start < hi and char_start < seg.char_end:
                if enclosing is not None:
                    return AMBIGUOUS
                if char_start < seg.char_start or char_end > seg.char_end:
                    return AMBIGUOUS
                enclosing = seg
        if enclosing is None:
            return AMBIGUOUS
        return enclosing

    def chapeau_of(
        self, segment: "StructuralSegment"
    ) -> "StructuralSegment | None":
        """The governing chapeau of a ``list_item`` (its ``parent_index``), or None.

        This is the LIST-INHERITANCE accessor: given a list item, return the
        chapeau it inherits from. Returns ``None`` for a segment that is not a
        list item or whose parent is not a chapeau.
        """
        if segment.kind != "list_item" or segment.parent_index is None:
            return None
        parent = self.segments[segment.parent_index]
        return parent if parent.kind == "chapeau" else None

    def coverage(self) -> "SegmentationCoverage":
        """Token-ownership census over the partition (the no-silent-drop report).

        Returns the char totals owned by interpreted segments vs explicit
        residual segments, plus the residual reasons encountered. Because the
        partition invariant is enforced at construction, ``interpreted_chars +
        residual_chars == text_len`` always holds (no unowned chars exist).
        """
        residual_chars = 0
        residual_reasons: dict[str, int] = {}
        for seg in self.segments:
            if seg.kind == "residual":
                residual_chars += seg.char_end - seg.char_start
                residual_reasons[seg.residual_reason] = (
                    residual_reasons.get(seg.residual_reason, 0) + 1
                )
        return SegmentationCoverage(
            text_len=self.text_len,
            interpreted_chars=self.text_len - residual_chars,
            residual_chars=residual_chars,
            residual_reasons=tuple(sorted(residual_reasons.items())),
        )


@dataclass(frozen=True, slots=True)
class SegmentationCoverage:
    """The token-ownership census of a :class:`SegmentationGraph`.

    Attributes:
        text_len:          Total chars in the unit.
        interpreted_chars: Chars owned by non-residual (interpreted) segments.
        residual_chars:    Chars owned by explicit ``residual`` segments.
        residual_reasons:  Sorted ``(reason, count)`` pairs of residual segments.
    """

    text_len: int
    interpreted_chars: int
    residual_chars: int
    residual_reasons: Tuple[Tuple[str, int], ...]

    @property
    def interpreted_fraction(self) -> float:
        """Fraction of chars owned by interpreted segments (0.0 if empty)."""
        if self.text_len == 0:
            return 0.0
        return self.interpreted_chars / self.text_len


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

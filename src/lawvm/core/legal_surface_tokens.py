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
class ProvisionSpan:
    """The enclosing provision a contiguous char range of the body text sits in.

    Jurisdiction-NEUTRAL carrier. A :class:`ProvisionIndex` answers "which
    provision (§ / momentti / kohta) owns this span?" — the structural identity
    the flattened body text alone cannot recover, because the AKN ``<num>`` and
    container nesting are dropped by the body decode. This carrier re-attaches
    that identity, sourced from the AKN structure (eId + ``<num>``), NEVER guessed
    from the flattened text.

    The provision PATH is carried two ways, both source-derived:

      * the canonical ``eid`` of the deepest enclosing provision container
        (``chp_1__sec_3__subsec_1__para_2`` etc.) — the same AKN eId form the
        reference resolver parses, so a consumer can join straight to it;
      * the decomposed labels (``chapter_label`` / ``section_label`` /
        ``subsection_num`` / ``item_label``) for the levels present, so a
        consumer can build a ``§/momentti/kohta`` path without re-parsing the
        eId. Each is the version-stripped label (``5``, ``115a``, ``1``); a level
        not present in the ancestry is ``None``/``""``.

    A span whose enclosing provision could NOT be mapped from the structure (no
    eId AND no ``<num>`` on any ancestor) carries ``mapped=False`` with an empty
    path and a non-empty ``unmapped_reason`` — the fail-loud witness (never a
    fabricated path). ``residual`` ranges (the join whitespace between body
    paragraphs) are NOT provision spans; they are simply absent from the index,
    and a query that lands on one returns :data:`AMBIGUOUS`.

    Attributes:
        char_start:     0-based inclusive offset into the unit body text.
        char_end:       0-based exclusive offset into the unit body text.
        eid:            Canonical AKN eId of the deepest enclosing provision
                        container, or ``""`` when unmapped.
        chapter_label:  Bare chapter (``luku``) label, or ``""``.
        section_label:  Bare section (``§``) label, or ``""``.
        subsection_num: Momentti number (1-based), or ``None``.
        item_label:     Bare kohta/item label (``1``, ``a``), or ``""``.
        mapped:         True iff the path was recovered from the AKN structure.
        unmapped_reason: Non-empty self-evidencing reason iff ``mapped`` is False;
                        ``""`` when mapped (the no-silent-drop witness).
    """

    char_start: int
    char_end: int
    eid: str = ""
    chapter_label: str = ""
    section_label: str = ""
    subsection_num: "int | None" = None
    item_label: str = ""
    mapped: bool = True
    unmapped_reason: str = ""

    def __post_init__(self) -> None:
        if self.char_start < 0:
            raise ValueError("ProvisionSpan.char_start must be >= 0")
        if self.char_end < self.char_start:
            raise ValueError("ProvisionSpan.char_end must be >= char_start")
        if self.subsection_num is not None and self.subsection_num < 1:
            raise ValueError("ProvisionSpan.subsection_num must be >= 1 or None")
        if self.mapped:
            if self.unmapped_reason:
                raise ValueError(
                    "a mapped ProvisionSpan must leave unmapped_reason empty"
                )
        else:
            if not self.unmapped_reason:
                raise ValueError(
                    "an unmapped ProvisionSpan MUST carry a non-empty "
                    "unmapped_reason (fail-loud: no fabricated provision path)"
                )
            if self.eid or self.section_label or self.subsection_num is not None:
                raise ValueError(
                    "an unmapped ProvisionSpan must carry no provision path"
                )

    def provision_path(self) -> str:
        """A compact ``§/momentti/kohta`` path of the present levels, or ``""``.

        ``6/1`` is "§6 momentti 1"; ``3/1/2`` adds kohta 2; ``5`` is the bare
        section. Returns ``""`` when unmapped or when no §-level label is present
        (the span sits above the section level, e.g. a chapter heading). This is
        the human-facing adjudication key; consumers needing the canonical
        identity use ``eid``.
        """
        if not self.mapped or not self.section_label:
            return ""
        parts = [self.section_label]
        if self.subsection_num is not None:
            parts.append(str(self.subsection_num))
            if self.item_label:
                parts.append(self.item_label)
        return "/".join(parts)


@dataclass(frozen=True, slots=True)
class ProvisionIndex:
    """Maps body-text char ranges to their enclosing AKN provision.

    Jurisdiction-NEUTRAL carrier + query harness, a SIBLING of
    :class:`SegmentationGraph` (one indexes structural line-shapes; this indexes
    provision identity). It re-attaches the §/momentti/kohta structure the body
    decode drops, so a consumer can ask "which provision owns this span?" —
    enabling enclosing-section anaphora (``Tätä pykälää ei sovelleta…``) and
    span-scoped composition that the flattened text alone cannot support.

    The :class:`ProvisionSpan` entries are the body paragraphs (one per decoded
    ``<p>``), in document order and non-overlapping. Unlike
    :class:`SegmentationGraph` this is NOT a total partition: the join
    whitespace BETWEEN paragraphs is not a provision and is simply absent (a
    query there returns :data:`AMBIGUOUS`). Totality of the *provision-bearing*
    content is instead reported by :meth:`coverage` (mapped vs unmapped chars).

    Attributes:
        source_unit_id: The ``SourceSurfaceUnit.source_unit_id`` this indexes.
        text_hash:      Hash of the exact body text the index was built over
                        (drift anchor; same convention as :class:`TokenTape`).
        spans:          Document-order, non-overlapping provision spans.
    """

    source_unit_id: str
    text_hash: str
    spans: Tuple[ProvisionSpan, ...]

    def __post_init__(self) -> None:
        prev_end = 0
        for sp in self.spans:
            if sp.char_start < prev_end:
                raise ValueError(
                    "ProvisionIndex.spans must be non-overlapping and in "
                    f"document order: span at {sp.char_start} starts before "
                    f"{prev_end}"
                )
            prev_end = sp.char_end

    def provision_at(
        self, char_start: int, char_end: int
    ) -> "ProvisionSpan | _Ambiguous":
        """Return the enclosing :class:`ProvisionSpan` for a char span.

        Mirrors :meth:`ClauseIndex.clause_at`: a span fully inside one provision
        span returns it; a span that crosses a provision boundary (or lands on a
        between-paragraph gap that is not a provision) returns :data:`AMBIGUOUS`
        — never silently bucketed. An empty span is located by point position.
        """
        if char_end < char_start:
            raise ValueError("query char_end must be >= char_start")
        hi = char_end if char_end > char_start else char_start + 1
        enclosing: ProvisionSpan | None = None
        for sp in self.spans:
            if sp.char_start < hi and char_start < sp.char_end:
                if enclosing is not None:
                    return AMBIGUOUS
                if char_start < sp.char_start or char_end > sp.char_end:
                    return AMBIGUOUS
                enclosing = sp
        if enclosing is None:
            return AMBIGUOUS
        return enclosing

    def coverage(self) -> "ProvisionCoverage":
        """Census over the provision spans (the recovery / fail-loud report).

        Reports how many provision spans (and how many chars) were mapped to a
        provision from the AKN structure vs left unmapped (fail-loud). A high
        mapped fraction means the §/momentti/kohta identity was recovered for
        most of the body's provision-bearing text.
        """
        mapped_spans = 0
        mapped_chars = 0
        unmapped_spans = 0
        unmapped_chars = 0
        unmapped_reasons: dict[str, int] = {}
        for sp in self.spans:
            width = sp.char_end - sp.char_start
            if sp.mapped:
                mapped_spans += 1
                mapped_chars += width
            else:
                unmapped_spans += 1
                unmapped_chars += width
                unmapped_reasons[sp.unmapped_reason] = (
                    unmapped_reasons.get(sp.unmapped_reason, 0) + 1
                )
        return ProvisionCoverage(
            total_spans=len(self.spans),
            mapped_spans=mapped_spans,
            mapped_chars=mapped_chars,
            unmapped_spans=unmapped_spans,
            unmapped_chars=unmapped_chars,
            unmapped_reasons=tuple(sorted(unmapped_reasons.items())),
        )


@dataclass(frozen=True, slots=True)
class ProvisionCoverage:
    """The mapped-vs-unmapped census of a :class:`ProvisionIndex`.

    Attributes:
        total_spans:      Number of provision spans (body paragraphs).
        mapped_spans:     Spans whose provision path was recovered.
        mapped_chars:     Chars owned by mapped spans.
        unmapped_spans:   Spans left fail-loud (no structure to map).
        unmapped_chars:   Chars owned by unmapped spans.
        unmapped_reasons: Sorted ``(reason, count)`` of unmapped spans.
    """

    total_spans: int
    mapped_spans: int
    mapped_chars: int
    unmapped_spans: int
    unmapped_chars: int
    unmapped_reasons: Tuple[Tuple[str, int], ...]

    @property
    def mapped_fraction(self) -> float:
        """Fraction of provision spans that were mapped (0.0 if empty)."""
        if self.total_spans == 0:
            return 0.0
        return self.mapped_spans / self.total_spans


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


#: Three honest states a token can be in w.r.t. a :class:`MorphOverlay`. A
#: consumer reading the overlay distinguishes them via
#: :meth:`MorphOverlay.token_status` — NEVER by annotation-absence alone, which
#: conflates "analyzed, no lemma" with "never analyzed".
#:
#:   * ``"annotated"``     — the token was analyzed AND maps to >= 1 lemma; the
#:                           :class:`MorphAnnotation` is present.
#:   * ``"analyzed_empty"`` — the token WAS run through the analyzer but its
#:                           surface is outside the closed vocabulary (a genuine
#:                           "no known lemma" negative fact, not an absence).
#:   * ``"not_analyzed"``  — the token was NEVER offered to the analyzer (e.g. a
#:                           non-word token, or a token outside the analyzed set);
#:                           the overlay says NOTHING about its lemma.
TOKEN_MORPH_STATUSES: frozenset[str] = frozenset(
    {"annotated", "analyzed_empty", "not_analyzed"}
)


@dataclass(frozen=True, slots=True)
class MorphCoverage:
    """The analyzed-vs-annotated census of a :class:`MorphOverlay`.

    The honest-by-accounting report that lets a consumer reason about
    annotation ABSENCE: ``analyzed_tokens`` is how many tokens were offered to
    the analyzer, of which ``annotated_tokens`` mapped to a lemma; the
    remainder (``analyzed_tokens - annotated_tokens``) are genuine "analyzed,
    no known lemma" negatives — distinct from the tokens never analyzed at all.

    Attributes:
        total_tokens:     Total tokens on the tape the overlay views.
        analyzed_tokens:  Tokens run through the analyzer (the analyzed set).
        annotated_tokens: Analyzed tokens that mapped to >= 1 lemma.
        vocab_fingerprint: Digest of the closed vocabulary absence was judged
                          against (so a stale/different vocabulary is detectable).
    """

    total_tokens: int
    analyzed_tokens: int
    annotated_tokens: int
    vocab_fingerprint: str

    @property
    def annotated_fraction(self) -> float:
        """Fraction of ANALYZED tokens that were annotated (0.0 if none analyzed)."""
        if self.analyzed_tokens == 0:
            return 0.0
        return self.annotated_tokens / self.analyzed_tokens


@dataclass(frozen=True, slots=True)
class MorphOverlay:
    """A SPARSE per-token reverse-morphology view over one :class:`TokenTape`.

    Maps ``token_index -> MorphAnnotation`` for the tokens whose surface inverts
    to a known lemma. The overlay is deliberately sparse, but it is honest BY
    ACCOUNTING, not merely by emptiness: alongside the annotations it records
    the SET OF TOKENS THAT WERE ANALYZED (``analyzed_token_indices``) and a
    fingerprint of the closed vocabulary (``vocab_fingerprint``). A consumer can
    therefore split annotation-absence into two genuinely different facts:

      * a token IN ``analyzed_token_indices`` but with no annotation was
        analyzed and has NO KNOWN LEMMA (a negative fact about the closed
        vocabulary);
      * a token NOT in ``analyzed_token_indices`` was NEVER analyzed — the
        overlay says nothing about it.

    Reading absence as "no lemma exists" without consulting the analyzed set is
    the bug this account closes. Use :meth:`token_status` / :meth:`coverage`
    rather than ``token_index not in annotations``.

    The overlay is not a general lemmatizer; the analyzed set is only the tokens
    the jurisdiction analyzer offered to its closed vocabulary (e.g. Finnish
    ``word``-category tokens), and the vocabulary itself is closed.

    Attributes:
        source_unit_id: The unit whose tape this overlay annotates (drift anchor).
        text_hash:      The tape's ``text_hash`` the overlay was built over.
        total_tokens:   ``len(tape.tokens)`` — the full token count the analyzed
                        set is a subset of (lets ``not_analyzed`` be total, not
                        just "absent from the analyzed set we happened to record").
        analyzed_token_indices: The token indices that WERE offered to the
                        analyzer. A superset of ``annotations`` keys: an analyzed
                        token with no annotation is a genuine "no known lemma".
        vocab_fingerprint: Stable digest of the closed vocabulary the analyzer
                        used (e.g. ``LemmaIndex.fingerprint()``). The
                        absence-of-annotation anchor: a consumer can detect it is
                        reading absence against a DIFFERENT vocabulary.
        annotations:    ``token_index -> MorphAnnotation`` for annotated tokens
                        ONLY (sparse). Every value's ``token_index`` equals its key.
    """

    source_unit_id: str
    text_hash: str
    total_tokens: int
    analyzed_token_indices: frozenset[int]
    vocab_fingerprint: str
    annotations: Mapping[int, MorphAnnotation]

    def __post_init__(self) -> None:
        if self.total_tokens < 0:
            raise ValueError("MorphOverlay.total_tokens must be >= 0")
        for index in self.analyzed_token_indices:
            if not 0 <= index < self.total_tokens:
                raise ValueError(
                    "MorphOverlay.analyzed_token_indices entry out of range: "
                    f"{index} not in [0, {self.total_tokens})"
                )
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
            if index not in self.analyzed_token_indices:
                raise ValueError(
                    "MorphOverlay annotation for an UN-analyzed token "
                    f"(key={index}): every annotated token must be in "
                    "analyzed_token_indices (you cannot annotate a token you "
                    "never analyzed)"
                )

    def token_status(self, token_index: int) -> str:
        """The honest morph status of ``token_index`` (a :data:`TOKEN_MORPH_STATUSES`).

        Distinguishes ``"annotated"`` / ``"analyzed_empty"`` / ``"not_analyzed"``
        — the whole point of the coverage account. A consumer MUST use this (or
        the explicit predicates below) instead of ``token_index in annotations``
        when it cares whether absence means "no known lemma" or "never looked".
        """
        if token_index in self.annotations:
            return "annotated"
        if token_index in self.analyzed_token_indices:
            return "analyzed_empty"
        return "not_analyzed"

    def was_analyzed(self, token_index: int) -> bool:
        """True iff ``token_index`` was offered to the analyzer.

        An absent annotation on a ``was_analyzed`` token is a genuine "no known
        lemma"; an absent annotation on a token that was NOT analyzed carries no
        information about its lemma.
        """
        return token_index in self.analyzed_token_indices

    def coverage(self) -> "MorphCoverage":
        """Analyzed-vs-annotated census (the honest-by-accounting report)."""
        return MorphCoverage(
            total_tokens=self.total_tokens,
            analyzed_tokens=len(self.analyzed_token_indices),
            annotated_tokens=len(self.annotations),
            vocab_fingerprint=self.vocab_fingerprint,
        )

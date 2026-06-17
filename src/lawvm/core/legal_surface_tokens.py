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

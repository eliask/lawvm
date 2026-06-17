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
from typing import Tuple

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

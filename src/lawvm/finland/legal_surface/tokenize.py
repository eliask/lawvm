"""Finnish-aware tokenizer producing a lossless :class:`TokenTape` (Phase 7).

Authoritative design: ``notes_internal/pro_on_fi_theory_grammar5.txt`` §D4.

:func:`build_token_tape` segments a source unit's ``raw_text`` into the
jurisdiction-neutral :class:`Token` categories from
``lawvm.core.legal_surface_tokens``. The segmentation is:

  - **deterministic** — same input, same output;
  - **total** — every character is accounted for by exactly one token (whitespace
    runs are emitted as ``whitespace`` tokens, so the tape contiguously covers
    ``raw_text``); and
  - **span-exact** — ``raw_text[t.char_start:t.char_end] == t.text`` for every
    token (enforced by :class:`Token` itself).

Finnish specifics handled here (NOT in core):
  - words use the Finnish letter class ``[A-Za-zÄÖÅäöå]`` (hyphenated compounds
    are split: the hyphen is its own ``dash`` token, mirroring the recognizer's
    word-boundary view where ``-`` is not a word char);
  - numbers are digit runs, with internal ``.`` permitted for dotted dates and
    decimals (``12.5``, ``1.1.2020``) — a trailing ``.`` is NOT swallowed
    (sentence period stays ``punct``);
  - ``§`` is a ``section_mark``; a ``§`` immediately followed by ``:`` + a case
    suffix (``§:n``, ``§:ssä``, ``§:ää`` …) is a single ``colon_suffix`` token;
  - en-dash ``–``, em-dash ``—`` and hyphen ``-`` are ``dash`` tokens;
  - all other non-space, non-word, non-dash characters are ``punct``.
"""
from __future__ import annotations

import hashlib

from lawvm.core.legal_surface_tokens import Token, TokenTape

#: Finnish word characters (the recognizer's word-char class is ASCII + äöåÄÖÅ).
_WORD_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZäöåÄÖÅ")
_DIGITS = set("0123456789")
_DASHES = set("-‐‑‒–—―")  # hyphen, NB-hyphen, figure/en/em/horiz
_SECTION = "§"  # §


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tok(text: str, start: int, end: int, category: str) -> Token:
    return Token(
        text=text,
        char_start=start,
        char_end=end,
        normalized=text.casefold(),
        category=category,
    )


def build_token_tape(source_unit_id: str, raw_text: str) -> TokenTape:
    """Tokenize ``raw_text`` into a lossless TokenTape (Finnish-aware).

    Total and span-exact: concatenating ``t.text`` in order reproduces
    ``raw_text`` exactly.
    """
    tokens: list[Token] = []
    n = len(raw_text)
    i = 0
    while i < n:
        ch = raw_text[i]

        # whitespace run
        if ch.isspace():
            j = i + 1
            while j < n and raw_text[j].isspace():
                j += 1
            tokens.append(_tok(raw_text[i:j], i, j, "whitespace"))
            i = j
            continue

        # section mark, possibly a "§:n"-style colon suffix
        if ch == _SECTION:
            j = i + 1
            if (
                j < n
                and raw_text[j] == ":"
                and j + 1 < n
                and raw_text[j + 1] in _WORD_CHARS
            ):
                # §:n / §:ssä / §:ää — consume ":" + the suffix word run
                k = j + 1
                while k < n and raw_text[k] in _WORD_CHARS:
                    k += 1
                tokens.append(_tok(raw_text[i:k], i, k, "colon_suffix"))
                i = k
            else:
                tokens.append(_tok(raw_text[i:j], i, j, "section_mark"))
                i = j
            continue

        # word run
        if ch in _WORD_CHARS:
            j = i + 1
            while j < n and raw_text[j] in _WORD_CHARS:
                j += 1
            tokens.append(_tok(raw_text[i:j], i, j, "word"))
            i = j
            continue

        # number run (digits with internal dots for dates/decimals)
        if ch in _DIGITS:
            j = i + 1
            while j < n:
                c = raw_text[j]
                if c in _DIGITS:
                    j += 1
                    continue
                # internal dot only if a digit follows (don't swallow sentence ".")
                if c == "." and j + 1 < n and raw_text[j + 1] in _DIGITS:
                    j += 1
                    continue
                break
            tokens.append(_tok(raw_text[i:j], i, j, "number"))
            i = j
            continue

        # dash (single char)
        if ch in _DASHES:
            tokens.append(_tok(raw_text[i : i + 1], i, i + 1, "dash"))
            i += 1
            continue

        # everything else: punctuation (single char)
        tokens.append(_tok(raw_text[i : i + 1], i, i + 1, "punct"))
        i += 1

    return TokenTape(
        source_unit_id=source_unit_id,
        text_hash=_sha256_text(raw_text),
        tokens=tuple(tokens),
    )

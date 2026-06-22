"""lexer — Raw text to token list conversion for Finnish amendment clauses.

This module owns:
  - tokenize(): raw text -> list[Token]
  - _emit_token(): classify one raw fragment and append Token(s)
  - witness_char_span(): convert ParseWitness token-index span to character span

Imports vocabulary and regex patterns from lexicon.py.
"""

from __future__ import annotations

from functools import lru_cache
import re

from lawvm.finland.johtolause.lexicon import (
    Token,
    _VOCAB,
    _CITE_RE,
    _YEAR_NUM_RE,
    _DASH_ONLY_RE,
    _RANGE_RE,
    _ROMAN_RE,
    _LETTER_RE,
    _NUM_RE,
    _SPLIT_RE,
    _GEN_NUM_RE,
    _TRANSLATIVE_NUM_RE,
    _NPYKALA_RE,
    _LETTER_PYKALA_RE,
    _NUM_DASH_STRUCT_RE,
    _LETTER_DASH_STRUCT_RE,
    _LETTER_DASH_NUM_RE,
    _LETTER_RANGE_RE,
    _case_from_pykala_suffix,
)
from lawvm.core.parse_witness import ParseWitness
from lawvm.finland.source_verb import SourceVerb

_SPLIT_PYKALA_SUFFIXES = frozenset({"n", "in", "en", "ään", "iin", "aan", "een", "ää", "ssä", "stä", "ksi"})
_WHITESPACE_RE = re.compile(r"\s+")
_PYKALA_APOSTROPHE_RE = re.compile(r"§:'")
_MISSING_PYKALA_RE = re.compile(
    r"(\d+):n\s+(\d+)\s+(momentti|momentin|kohta|kohdan)\b"
)
_GLUED_NUMERIC_CONJ_RE = re.compile(r"^(\d+[a-z]?)(ja|sekä)$", re.I)
_PLAIN_NUMERIC_LABEL_RE = re.compile(r"^\d+[a-z]?$", re.I)


def tokenize(text: str) -> list[Token]:
    """Tokenize Finnish amendment clause text into classified tokens."""
    return list(_tokenize_tuple(text))


@lru_cache(maxsize=8192)
def _tokenize_tuple(text: str) -> tuple[Token, ...]:
    """Tokenize Finnish amendment clause text into classified tokens.

    No NLP model needed.  Uses a ~120-entry vocabulary lookup table plus
    regex patterns for numbers, punctuation, and compound splits.

    Each returned Token carries char_start and char_end offsets into the
    normalized input string (after whitespace collapse).  These offsets
    support character-level span highlighting in the viewer.
    """
    # Normalize whitespace — offsets are relative to this normalized form
    text = _WHITESPACE_RE.sub(" ", text).strip()
    # Normalize §-suffix apostrophes: "§:'ään" → "§:ään" (Finlex XML artifact)
    text = _PYKALA_APOSTROPHE_RE.sub("§:", text)
    # Normalize missing § in "N:n M momentti/kohta" patterns (source pathology).
    # Some johtolause texts accidentally omit the § sign before the genitive suffix,
    # e.g. "94:n 1 momentti" instead of "94 §:n 1 momentti".  Restore the §.
    text = _MISSING_PYKALA_RE.sub(r"\1 §:n \2 \3", text)

    # Split into raw fragments, tracking their positions in the normalized text
    raw_parts: list[tuple[str, int]] = []  # (fragment, char_start)
    last = 0
    for m in _SPLIT_RE.finditer(text):
        if m.start() > last:
            raw_parts.append((text[last : m.start()], last))
        frag = m.group().strip()
        if frag:
            # The stripped fragment starts at the first non-space char of the match
            frag_offset = m.start() + (m.group().index(frag[0]) if frag else 0)
            raw_parts.append((frag, frag_offset))
        last = m.end()
    if last < len(text):
        raw_parts.append((text[last:], last))

    def _merged_split_pykala_token(idx: int) -> tuple[Token, int] | None:
        raw, char_off = raw_parts[idx]
        if raw != "§":
            return None

        if idx + 1 < len(raw_parts):
            suffix_raw, suffix_off = raw_parts[idx + 1]
            if suffix_raw.startswith(":") and suffix_raw[1:] in _SPLIT_PYKALA_SUFFIXES:
                suffix = suffix_raw
                return (
                    Token(
                        text="§" + suffix,
                        lemma="§",
                        cat="PYKALA",
                        case=_case_from_pykala_suffix(suffix),
                        verb_code=None,
                        char_start=char_off,
                        char_end=suffix_off + len(suffix_raw),
                    ),
                    idx + 2,
                )

        if idx + 2 < len(raw_parts):
            colon_raw, _colon_off = raw_parts[idx + 1]
            suffix_raw, suffix_off = raw_parts[idx + 2]
            if colon_raw == ":" and suffix_raw in _SPLIT_PYKALA_SUFFIXES:
                suffix = ":" + suffix_raw
                return (
                    Token(
                        text="§" + suffix,
                        lemma="§",
                        cat="PYKALA",
                        case=_case_from_pykala_suffix(suffix),
                        verb_code=None,
                        char_start=char_off,
                        char_end=suffix_off + len(suffix_raw),
                    ),
                    idx + 3,
                )

        return None

    def _split_glued_numeric_conjunction(idx: int) -> tuple[list[Token], int] | None:
        raw, char_off = raw_parts[idx]
        m = _GLUED_NUMERIC_CONJ_RE.match(raw)
        if not m:
            return None
        if idx + 2 >= len(raw_parts):
            return None

        next_raw = raw_parts[idx + 1][0]
        next_next_raw = raw_parts[idx + 2][0].lower()
        if not _PLAIN_NUMERIC_LABEL_RE.match(next_raw):
            return None
        if next_next_raw not in {"§", "momentti", "momentin", "kohta", "kohdan", "luku", "luvun"}:
            return None

        head = m.group(1)
        conj = m.group(2)
        split_tokens: list[Token] = []
        _emit_token(head, split_tokens, char_off)
        _emit_token(conj, split_tokens, char_off + len(head))
        return split_tokens, idx + 1

    # Classify each fragment
    tokens: list[Token] = []
    i = 0
    while i < len(raw_parts):
        merged = _merged_split_pykala_token(i)
        if merged is not None:
            token, i = merged
            tokens.append(token)
            continue

        split = _split_glued_numeric_conjunction(i)
        if split is not None:
            split_tokens, i = split
            tokens.extend(split_tokens)
            continue

        raw, char_off = raw_parts[i]
        if not raw or raw.isspace():
            i += 1
            continue
        _emit_token(raw, tokens, char_off)
        i += 1
    return tuple(_repair_lost_dash_section_range(tokens))


def _repair_lost_dash_section_range(tokens: list[Token]) -> list[Token]:
    """Restore an OCR-dropped dash in a bare two-number section range.

    Source pathology (e.g. 1987/320 ← 1994/1375 "21 23 §"): a section range
    "21–23 §" loses its dash in the scan, leaving two adjacent bare NUM tokens
    immediately before a single PYKALA.  A genuine list of separate targets
    always carries an explicit separator (",", "ja", "sekä") between the
    numbers, which tokenizes to a COMMA/CONJ token — so two *adjacent* plain
    NUM tokens before "§" with no separator can only be a coalesced range.

    Conservative guard: fire ONLY for the exact shape NUM NUM PYKALA where both
    numbers are plain digits (no LETTER suffix, no case) and strictly ascending.
    A descending or equal pair is left untouched (not a valid range; do not
    fabricate one).  Inserts a DASH token between the two numbers, producing the
    canonical NUM DASH NUM PYKALA shape the range grammar already consumes.
    """
    n = len(tokens)
    if n < 3:
        return tokens
    out: list[Token] = []
    i = 0
    while i < n:
        if (
            i + 2 < n
            and tokens[i].cat == "NUM"
            and tokens[i + 1].cat == "NUM"
            and tokens[i + 2].cat == "PYKALA"
            and tokens[i].text.isdigit()
            and tokens[i + 1].text.isdigit()
            and int(tokens[i].text) < int(tokens[i + 1].text)
        ):
            first = tokens[i]
            second = tokens[i + 1]
            dash_start = first.char_end if first.char_end >= 0 else -1
            dash_end = second.char_start if second.char_start >= 0 else -1
            out.append(first)
            out.append(
                Token(
                    text="–",
                    lemma="–",
                    cat="DASH",
                    case="",
                    verb_code=None,
                    char_start=dash_start,
                    char_end=dash_end,
                )
            )
            out.append(second)
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out


def _emit_token(raw: str, out: list[Token], char_offset: int = -1) -> None:
    """Classify one raw fragment and append Token(s) to out.

    char_offset is the position of `raw` in the normalized input string.
    When -1 (legacy/sentinel callers), char_start/char_end are left as -1.
    """
    low = raw.lower()

    # ---- Compound splits ----

    # Helper: emit a single Token with char offsets derived from position within raw.
    # sub_start/sub_end are byte indices into raw (half-open); they are added to
    # char_offset to produce absolute positions in the normalized input text.
    # When char_offset is -1 (sentinel/legacy caller), offsets are left as -1.
    def _tok(
        text_: str,
        lemma_: str,
        cat_: str,
        case_: str,
        verb_code_: SourceVerb | None,
        sub_start: int = 0,
        sub_end: int | None = None,
    ) -> Token:
        if sub_end is None:
            sub_end = sub_start + len(text_)
        if char_offset >= 0:
            return Token(text_, lemma_, cat_, case_, verb_code_, char_offset + sub_start, char_offset + sub_end)
        return Token(text_, lemma_, cat_, case_, verb_code_)

    if low and _DASH_ONLY_RE.match(low[0]):
        entry = _VOCAB.get(low[1:])
        if entry and entry[1] == "ALAKOHTA":
            out.append(_tok(raw, entry[0], entry[1], entry[2], entry[3], 0, len(raw)))
            return

    # N§:suffix (e.g. "20§:n", "16a§:ään")
    m = _NPYKALA_RE.match(raw)
    if m:
        num_part = m.group(1)
        suffix = m.group(3) or ""
        digits = num_part.rstrip("abcdefghijklmnopqrstuvwxyz")
        letter = num_part[len(digits) :]
        out.append(_tok(digits, digits, "NUM", "", None, 0, len(digits)))
        if letter:
            out.append(_tok(letter, letter, "LETTER", "", None, len(digits), len(num_part)))
        # Determine case from suffix
        case = _case_from_pykala_suffix(suffix)
        pyk_text = "\u00a7" + suffix
        pyk_start = len(num_part)
        out.append(_tok(pyk_text, "\u00a7", "PYKALA", case, None, pyk_start, pyk_start + len(pyk_text)))
        return

    # a§ or a§:n
    m = _LETTER_PYKALA_RE.match(raw)
    if m and not raw[0].isdigit():
        letter = m.group(1)
        pyk = m.group(2)
        out.append(_tok(letter, letter.lower(), "LETTER", "", None, 0, len(letter)))
        suffix = pyk[1:] if len(pyk) > 1 and ":" in pyk else ""
        case = _case_from_pykala_suffix(suffix)
        out.append(_tok(pyk, "\u00a7", "PYKALA", case, None, len(letter), len(letter) + len(pyk)))
        return

    # N-kohta, N-momentti
    m = _NUM_DASH_STRUCT_RE.match(raw)
    if m:
        num_part, struct = m.group(1), m.group(2).lower()
        out.append(_tok(num_part, num_part, "NUM", "", None, 0, len(num_part)))
        struct_start = raw.lower().index(struct, len(num_part))
        entry = _VOCAB.get(struct)
        if entry:
            out.append(_tok(struct, entry[0], entry[1], entry[2], None, struct_start, struct_start + len(struct)))
        else:
            out.append(_tok(struct, struct, "WORD", "", None, struct_start, struct_start + len(struct)))
        return

    # a-kohta (letter-dash-structural)
    m = _LETTER_DASH_STRUCT_RE.match(raw)
    if m:
        out.append(_tok(m.group(1), m.group(1).lower(), "LETTER", "", None, 0, len(m.group(1))))
        struct = m.group(2).lower()
        struct_start = raw.lower().index(struct, len(m.group(1)))
        entry = _VOCAB.get(struct)
        if entry:
            out.append(_tok(struct, entry[0], entry[1], entry[2], None, struct_start, struct_start + len(struct)))
        return

    # a—1 (letter-dash-number from range expressions)
    m = _LETTER_DASH_NUM_RE.match(raw)
    if m:
        g1, g2 = m.group(1), m.group(2)
        dash_start = len(g1)
        num_start = len(raw) - len(g2)
        out.append(_tok(g1, g1, "LETTER", "", None, 0, len(g1)))
        out.append(_tok("\u2013", "\u2013", "DASH", "", None, dash_start, num_start))
        out.append(_tok(g2, g2, "NUM", "", None, num_start, num_start + len(g2)))
        return

    # a-c (letter range for item enumerations, e.g. "j-l kohta")
    # Splits into LETTER DASH LETTER so the item-letter range grammar can
    # expand it (j-l -> j, k, l).  Without the split it stays a single WORD and
    # poisons the surrounding enumeration.
    m = _LETTER_RANGE_RE.match(raw)
    if m and not raw[0].isdigit():
        g1, g2 = m.group(1), m.group(2)
        dash_start = len(g1)
        let2_start = len(raw) - len(g2)
        out.append(_tok(g1, g1, "LETTER", "", None, 0, len(g1)))
        out.append(_tok("\u2013", "\u2013", "DASH", "", None, dash_start, let2_start))
        out.append(_tok(g2, g2, "LETTER", "", None, let2_start, let2_start + len(g2)))
        return

    # ---- NUM+LETTER compound (e.g. "14a", "5b") without § ----
    # Split into separate NUM and LETTER tokens
    if low and low[0].isdigit():
        i = 0
        while i < len(low) and low[i].isdigit():
            i += 1
        if i < len(low) and i > 0 and _LETTER_RE.match(low[i:]):
            out.append(_tok(raw[:i], raw[:i], "NUM", "", None, 0, i))
            out.append(_tok(raw[i:], low[i:], "LETTER", "", None, i, len(raw)))
            return

    # ---- Vocabulary lookup ----
    entry = _VOCAB.get(low)
    if entry:
        out.append(_tok(raw, entry[0], entry[1], entry[2], entry[3], 0, len(raw)))
        return

    # ---- Numbers ----
    if _NUM_RE.match(raw):
        out.append(_tok(raw, raw, "NUM", "", None, 0, len(raw)))
        return
    # Genitive number: "1:n" -> treat as NUM (the genitive is on the
    # following § token, not the number)
    m = _GEN_NUM_RE.match(raw)
    if m:
        out.append(_tok(m.group(1), m.group(1), "NUM", "", None, 0, len(m.group(1))))
        return

    # Translative number: "3:ksi" -> NUM (renumbering target)
    m = _TRANSLATIVE_NUM_RE.match(raw)
    if m:
        out.append(_tok(raw, m.group(1), "NUM", "TRANS", None, 0, len(raw)))
        return

    # Translative roman numeral: "III:ksi" -> NUM (renumbering target)
    if raw.lower().endswith(":ksi"):
        roman = raw[:-4]
        if _ROMAN_RE.match(roman.upper()) and len(roman) <= 6:
            out.append(_tok(raw, roman.upper(), "NUM", "TRANS", None, 0, len(raw)))
            return

    # Range in single token: "21-23"
    m = _RANGE_RE.match(raw)
    if m:
        g1, g2 = m.group(1), m.group(2)
        # dash occupies everything between the two numbers
        dash_start = len(g1)
        num2_start = len(raw) - len(g2)
        out.append(_tok(g1, g1, "NUM", "", None, 0, len(g1)))
        out.append(_tok("\u2013", "\u2013", "DASH", "", None, dash_start, num2_start))
        out.append(_tok(g2, g2, "NUM", "", None, num2_start, num2_start + len(g2)))
        return

    # Roman numerals (for part refs)
    if _ROMAN_RE.match(raw) and len(raw) <= 6:
        out.append(_tok(raw, raw.upper(), "NUM", "", None, 0, len(raw)))
        return

    # Single letter (section suffix like "a" in "5 a §")
    if _LETTER_RE.match(low):
        out.append(_tok(raw, low, "LETTER", "", None, 0, len(raw)))
        return

    # ---- Punctuation ----
    if raw == ",":
        out.append(_tok(",", ",", "COMMA", "", None, 0, 1))
        return
    if _DASH_ONLY_RE.match(raw):
        # A single dash OR a run of them (``--`` / ``---``, a Finlex/OCR variant
        # of an en-dash range such as ``21--23 \u00a7``).  Canonicalize the lemma to
        # en-dash (U+2013), matching the central _normalize_fi_parse_text
        # convention.  The grammar consumes DASH by category, not lemma, so this
        # is a convention-only change.
        out.append(_tok(raw, "\u2013", "DASH", "", None, 0, len(raw)))
        return
    if raw in ("(", ")", ":", ";", "."):
        out.append(_tok(raw, raw, "PUNCT", "", None, 0, len(raw)))
        return

    # ---- Citation (should be caught by filters, but just in case) ----
    if _CITE_RE.match(raw) or _YEAR_NUM_RE.match(raw):
        out.append(_tok(raw, raw, "CITE", "", None, 0, len(raw)))
        return

    # ---- Trailing period on known tokens ----
    # "momentti." at sentence end — strip period and re-classify
    if low.endswith(".") and len(low) > 1:
        stripped = low[:-1]
        entry = _VOCAB.get(stripped)
        if entry:
            out.append(_tok(raw[:-1], entry[0], entry[1], entry[2], entry[3], 0, len(raw) - 1))
            out.append(_tok(".", ".", "PUNCT", "", None, len(raw) - 1, len(raw)))
            return

    # ---- Everything else is a word ----
    out.append(_tok(raw, low, "WORD", "", None, 0, len(raw)))


def witness_char_span(
    witness: "ParseWitness",
    tokens: "list[Token]",
) -> "tuple[int, int] | None":
    """Convert a ParseWitness token-index span to a character span.

    The tokens list must be the same list that was passed to the parser
    (i.e. the filtered/structural view from apply_annotations, NOT the raw
    tokenize() output).  Tokens in that list that came from tokenize() carry
    char_start/char_end; sentinel tokens have char_start == -1.

    Returns (char_start, char_end) in the normalized input string, or None
    if the span is absent, out of range, or all covered tokens lack offsets.
    """
    if witness.source_span is None:
        return None
    start_idx, end_idx = witness.source_span
    n = len(tokens)
    if start_idx < 0 or end_idx > n or start_idx >= end_idx:
        return None

    # Collect all valid char positions from the spanned tokens
    valid_starts = [tokens[i].char_start for i in range(start_idx, end_idx) if tokens[i].char_start >= 0]
    valid_ends = [tokens[i].char_end for i in range(start_idx, end_idx) if tokens[i].char_end >= 0]
    if not valid_starts or not valid_ends:
        return None
    return (min(valid_starts), max(valid_ends))

"""UK source-text normalization surfaces.

These helpers build parser/comparison views over source text. They must not be
used to mutate stored source witnesses or replay payload text silently.
"""

from __future__ import annotations


_DASH_VARIANTS = {
    "\u2010",
    "\u2011",
    "\u2012",
    "\u2013",
    "\u2014",
    "\u2015",
    "\u2212",
}

_OPEN_TO_CLOSE_QUOTE = {
    "\u201c": "\u201d",
    "\u2018": "\u2019",
    '"': '"',
    "'": "'",
}

_OUTSIDE_QUOTE_TOKEN_JOIN_REPAIRS = (
    ("thereshall", "there shall"),
    ("thereis", "there is"),
    ("thereare", "there are"),
    ("beomitted", "be omitted"),
    ("berepealed", "be repealed"),
)


def _is_word_apostrophe(text: str, index: int) -> bool:
    if text[index] != "'":
        return False
    previous_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    return previous_char.isalnum() and next_char.isalnum()


def _repair_outside_quote_token_joins(segment: str) -> str:
    repaired = segment
    for joined, replacement in _OUTSIDE_QUOTE_TOKEN_JOIN_REPAIRS:
        repaired = repaired.replace(joined, replacement)
        repaired = repaired.replace(joined.capitalize(), replacement.capitalize())
    return repaired


def normalize_uk_parser_text(text: str) -> str:
    """Return a parser-only view of UK source text.

    The view collapses transport whitespace and canonicalizes dash-like
    instruction punctuation outside quoted legal text. Quoted payload/preimage
    text is preserved after the existing whitespace collapse so parser matching
    does not rewrite legal fragments such as ``"A-B"`` or ``"A-B-C"``.
    """
    collapsed = " ".join(str(text or "").split())
    out: list[str] = []
    outside_segment: list[str] = []
    quote_stack: list[str] = []
    def flush_outside_segment() -> None:
        if outside_segment:
            out.append(_repair_outside_quote_token_joins("".join(outside_segment)))
            outside_segment.clear()

    for index, char in enumerate(collapsed):
        if quote_stack:
            flush_outside_segment()
            out.append(char)
            if char == quote_stack[-1] and not _is_word_apostrophe(collapsed, index):
                quote_stack.pop()
            continue
        if _is_word_apostrophe(collapsed, index):
            outside_segment.append(char)
            continue
        closing_quote = _OPEN_TO_CLOSE_QUOTE.get(char)
        if closing_quote is not None:
            flush_outside_segment()
            quote_stack.append(closing_quote)
            out.append(char)
        elif char in _DASH_VARIANTS:
            outside_segment.append("\u2014")
        else:
            outside_segment.append(char)
    flush_outside_segment()
    return "".join(out).strip()
